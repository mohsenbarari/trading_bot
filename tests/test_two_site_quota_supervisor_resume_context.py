"""Real-file positive, STOP, and NOT_DUE tests for supervisor resume context."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
import tempfile
from typing import Any
import unittest.mock

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

try:
    from tests.test_p1_00_characterization_network_isolation import (
        install_p1_00_network_fence,
    )
except ImportError:
    from test_p1_00_characterization_network_isolation import (  # type: ignore
        install_p1_00_network_fence,
    )

install_p1_00_network_fence()

from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_provider_broker import broker_paths
from scripts.two_site_quota_supervisor_resume_context import (
    ResumeContextError,
    collect_known_reset_resume_context_locked,
)
from scripts.two_site_quota_wait_admission import admit_initial_quota_wait_locked
from scripts.two_site_quota_wait_sources import collect_initial_wait_source_expected
from scripts.two_site_quota_wait_storage import read_initial_wait_record_bytes
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_real_source_join_fileproof import (
    _setup_scenario_through_receipt,
)
from tests.test_two_site_quota_supervisor_due_inspect import _setup_env_for_kind
from tests.test_two_site_quota_wait_admission import (
    _patch_repo_boundary,
    fake_clock,
)


def _collect_locked(env: dict[str, Any], timestamp: str) -> dict[str, Any]:
    with exclusive_supervisor_lock(env["autopilot_dir"] / "supervisor.lock"):
        return collect_known_reset_resume_context_locked(
            env["root"], env["original_ctrl_id"], timestamp
        )


def _admit_wait(sc: dict[str, Any]) -> dict[str, Any]:
    with exclusive_supervisor_lock(sc["autopilot_dir"] / "supervisor.lock"):
        return admit_initial_quota_wait_locked(
            sc["root"],
            sc["original_ctrl_id"],
            clock=lambda: sc["now_utc"] - timedelta(seconds=120),
        )


def test_quota_supervisor_resume_context_positive(broker_repo: Any) -> None:
    sc = _setup_scenario_through_receipt(*broker_repo)
    req_dir = broker_paths(sc["root"])[2]
    initial_requests = sorted(os.listdir(req_dir))
    claim_path = sc["runs_dir"] / "quota-resume-claim.json"
    sub_path = sc["runs_dir"] / "quota-resume-submission.json"
    assert not claim_path.exists() and not sub_path.exists()

    wait_record = _admit_wait(sc)
    with exclusive_supervisor_lock(sc["autopilot_dir"] / "supervisor.lock"):
        expected_wait = collect_initial_wait_source_expected(
            sc["root"], sc["original_ctrl_id"], sc["intent_epoch"], 1
        )
        raw_wait_bytes = read_initial_wait_record_bytes(
            sc["root"], sc["original_ctrl_id"]
        )

    next_check = datetime.fromisoformat(
        wait_record["schedule"]["next_check_at_utc"].replace("Z", "+00:00")
    )
    timestamp = (next_check + timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    res = _collect_locked(sc, timestamp)
    assert set(res) == {
        "original_controller_run_id",
        "wait_record",
        "expected_wait",
        "raw_wait_bytes",
        "semantic_attempt",
        "expected_task_id",
        "due",
    }
    assert res["original_controller_run_id"] == sc["original_ctrl_id"]
    assert res["wait_record"] == wait_record
    assert res["expected_wait"] == expected_wait
    assert res["raw_wait_bytes"] == raw_wait_bytes
    assert res["semantic_attempt"]["fingerprint"] == sc["identity"]["fingerprint"]
    assert res["expected_task_id"] == sc["loaded_task"]["task_id"]
    assert res["due"]["state"] == "KNOWN_RESET_REVALIDATION_DUE"

    assert not claim_path.exists() and not sub_path.exists()
    assert sorted(os.listdir(req_dir)) == initial_requests


def test_quota_supervisor_resume_context_stop(
    broker_repo: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sc = _setup_scenario_through_receipt(*broker_repo)
    wait_record = _admit_wait(sc)
    req_dir = broker_paths(sc["root"])[2]
    initial_requests = sorted(os.listdir(req_dir))
    claim_path = sc["runs_dir"] / "quota-resume-claim.json"
    sub_path = sc["runs_dir"] / "quota-resume-submission.json"
    assert not claim_path.exists() and not sub_path.exists()
    next_check = datetime.fromisoformat(
        wait_record["schedule"]["next_check_at_utc"].replace("Z", "+00:00")
    )
    timestamp = (next_check + timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    (sc["autopilot_dir"] / "STOP").write_text("STOP\n")

    mock_load = unittest.mock.MagicMock()
    mock_semantic = unittest.mock.MagicMock()
    monkeypatch.setattr(
        "scripts.two_site_quota_supervisor_resume_context.load_task", mock_load
    )
    monkeypatch.setattr(
        "scripts.two_site_quota_supervisor_resume_context.read_existing_semantic_attempt",
        mock_semantic,
    )

    with pytest.raises(ResumeContextError) as exc_info:
        _collect_locked(sc, timestamp)

    assert "autopilot STOP sentinel exists" in str(exc_info.value)
    mock_load.assert_not_called()
    mock_semantic.assert_not_called()
    assert not claim_path.exists() and not sub_path.exists()
    assert sorted(os.listdir(req_dir)) == initial_requests


def test_quota_supervisor_resume_context_not_due(
    broker_repo: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sc = _setup_scenario_through_receipt(*broker_repo)
    wait_record = _admit_wait(sc)
    req_dir = broker_paths(sc["root"])[2]
    initial_requests = sorted(os.listdir(req_dir))
    claim_path = sc["runs_dir"] / "quota-resume-claim.json"
    sub_path = sc["runs_dir"] / "quota-resume-submission.json"
    assert not claim_path.exists() and not sub_path.exists()
    created_at_utc = wait_record["schedule"]["created_at_utc"]

    mock_load = unittest.mock.MagicMock()
    mock_semantic = unittest.mock.MagicMock()
    monkeypatch.setattr(
        "scripts.two_site_quota_supervisor_resume_context.load_task", mock_load
    )
    monkeypatch.setattr(
        "scripts.two_site_quota_supervisor_resume_context.read_existing_semantic_attempt",
        mock_semantic,
    )

    with pytest.raises(ResumeContextError) as exc_info:
        _collect_locked(sc, created_at_utc)

    assert "NOT_DUE" in str(exc_info.value)
    mock_load.assert_not_called()
    mock_semantic.assert_not_called()
    assert not claim_path.exists() and not sub_path.exists()
    assert sorted(os.listdir(req_dir)) == initial_requests


def test_quota_supervisor_resume_context_unknown_quota_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        env = _setup_env_for_kind(tmp_dir, "QUOTA_RESET_UNKNOWN")
        root, cid = env["root"], env["request"]["controller_run_id"]
        env["autopilot_dir"] = root / ".local" / "two-site-refactor" / "autopilot"
        env["original_ctrl_id"] = cid

        p_intent, p_sources = _patch_repo_boundary(env["facts"])
        with p_intent, p_sources:
            with exclusive_supervisor_lock(env["autopilot_dir"] / "supervisor.lock"):
                rec = admit_initial_quota_wait_locked(root, cid, clock=fake_clock)
            wait_file = env["autopilot_dir"] / "runs" / cid / "quota-wait.json"
            assert json.loads(wait_file.read_text())["schedule"]["reset_kind"] == "QUOTA_RESET_UNKNOWN"
            assert rec["schedule"]["reset_kind"] == "QUOTA_RESET_UNKNOWN"

            run_dir = root / ".local/two-site-refactor/autopilot/runs" / cid
            req_dir = broker_paths(root)[2]
            initial_requests = sorted(os.listdir(req_dir))
            claim_path = run_dir / "quota-resume-claim.json"
            sub_path = run_dir / "quota-resume-submission.json"
            assert not claim_path.exists() and not sub_path.exists()

            mock_load, mock_sem = unittest.mock.MagicMock(), unittest.mock.MagicMock()
            monkeypatch.setattr("scripts.two_site_quota_supervisor_resume_context.load_task", mock_load)
            monkeypatch.setattr("scripts.two_site_quota_supervisor_resume_context.read_existing_semantic_attempt", mock_sem)

            with pytest.raises(ResumeContextError) as exc_info:
                _collect_locked(env, rec["schedule"]["next_check_at_utc"])

            assert "UNKNOWN_BACKOFF_REVIEW_DUE" in str(exc_info.value)
            mock_load.assert_not_called()
            mock_sem.assert_not_called()
            assert not claim_path.exists() and not sub_path.exists()
            assert sorted(os.listdir(req_dir)) == initial_requests


def test_quota_supervisor_resume_context_task_byte_drift(
    broker_repo: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sc = _setup_scenario_through_receipt(*broker_repo)
    req_dir = broker_paths(sc["root"])[2]
    initial_requests = sorted(os.listdir(req_dir))
    claim_path = sc["runs_dir"] / "quota-resume-claim.json"
    sub_path = sc["runs_dir"] / "quota-resume-submission.json"
    wait_record = _admit_wait(sc)
    next_check = datetime.fromisoformat(wait_record["schedule"]["next_check_at_utc"].replace("Z", "+00:00"))
    timestamp = (next_check + timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    task_path = sc["task"]
    task_path.write_bytes(task_path.read_bytes() + b"\n# drift\n")

    mock_load, mock_sem = unittest.mock.MagicMock(), unittest.mock.MagicMock()
    monkeypatch.setattr("scripts.two_site_quota_supervisor_resume_context.load_task", mock_load)
    monkeypatch.setattr("scripts.two_site_quota_supervisor_resume_context.read_existing_semantic_attempt", mock_sem)

    with pytest.raises(ResumeContextError) as exc_info:
        _collect_locked(sc, timestamp)

    assert "terminal quota response not detected" in str(exc_info.value)
    mock_load.assert_not_called()
    mock_sem.assert_not_called()
    assert not claim_path.exists() and not sub_path.exists()
    assert sorted(os.listdir(req_dir)) == initial_requests


def test_quota_supervisor_resume_context_semantic_attempt_drift(
    broker_repo: Any,
) -> None:
    sc = _setup_scenario_through_receipt(*broker_repo)
    req_dir = broker_paths(sc["root"])[2]
    initial_requests = sorted(os.listdir(req_dir))
    claim_path = sc["runs_dir"] / "quota-resume-claim.json"
    sub_path = sc["runs_dir"] / "quota-resume-submission.json"
    wait_record = _admit_wait(sc)
    next_check = datetime.fromisoformat(wait_record["schedule"]["next_check_at_utc"].replace("Z", "+00:00"))
    timestamp = (next_check + timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    attempt_path = (
        sc["root"] / ".local" / "two-site-refactor" / "semantic-attempts" / f"{sc['identity']['fingerprint']}.json"
    )
    data = json.loads(attempt_path.read_text(encoding="utf-8"))
    data["task_id"] = "mutated-task-id"
    attempt_path.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(attempt_path, 0o600)

    with pytest.raises(ResumeContextError):
        _collect_locked(sc, timestamp)

    assert not claim_path.exists() and not sub_path.exists()
    assert sorted(os.listdir(req_dir)) == initial_requests


def test_quota_supervisor_resume_context_raw_wait_race(
    broker_repo: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sc = _setup_scenario_through_receipt(*broker_repo)
    req_dir = broker_paths(sc["root"])[2]
    initial_requests = sorted(os.listdir(req_dir))
    claim_path = sc["runs_dir"] / "quota-resume-claim.json"
    sub_path = sc["runs_dir"] / "quota-resume-submission.json"
    wait_record = _admit_wait(sc)
    next_check = datetime.fromisoformat(wait_record["schedule"]["next_check_at_utc"].replace("Z", "+00:00"))
    timestamp = (next_check + timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    mock_load = unittest.mock.MagicMock()
    monkeypatch.setattr("scripts.two_site_quota_supervisor_resume_context.load_task", mock_load)
    monkeypatch.setattr(
        "scripts.two_site_quota_supervisor_resume_context.read_initial_wait_record_bytes",
        lambda *args, **kwargs: b"tampered",
    )

    with pytest.raises(ResumeContextError) as exc_info:
        _collect_locked(sc, timestamp)

    assert "raw wait bytes do not match canonical wait record JSON" in str(exc_info.value)
    mock_load.assert_not_called()
    assert not claim_path.exists() and not sub_path.exists()
    assert sorted(os.listdir(req_dir)) == initial_requests



# The new READY reader is an isolated wait-only boundary test. Existing tests above
# retain real wait/claim/admission fixtures; real chain and terminal storage have
# their own whole-module tests in the combined native target.
@pytest.fixture
def ready_handoff(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from copy import deepcopy
    import hashlib
    from pathlib import Path
    from scripts import two_site_cycle_tools as tools
    from scripts import two_site_proposal_pipeline as pipeline
    from scripts import two_site_provider_broker as broker
    from scripts import two_site_quota_continuation_admission as admission
    from scripts import two_site_quota_continuation_terminal_storage as terminal
    from scripts import two_site_quota_supervisor_resume_context as context

    root = tmp_path.resolve()
    task_path = root / "task.json"
    task_path.write_bytes(b'{"task_id":"ready-handoff"}\n')
    task_path.chmod(0o600)
    request = {
        "schema_version": "1.0", "request_type": "QUOTA_CONTINUATION",
        "request_id": "a" * 32, "original_request_id": "b" * 32,
        "controller_run_id": "cycle-0002-20261020T120001Z-2",
        "original_controller_run_id": "cycle-0001-20261020T120000Z-1",
        "claim_sha256": "1" * 64, "original_wait_sha256": "2" * 64,
        "assignment_id": "WA-TEST-READY", "assignment_sha256": "3" * 64,
        "claimed_at_utc": "2026-10-20T12:00:01Z", "created_at": 1792497601.0,
        "task_path": "task.json",
        "task_id": "ready-handoff", "source_head": "c" * 40,
        "semantic_task_fingerprint": "d" * 64,
        "task_sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
        "role": "worker", "run_prefix": "qresume-ready", "timeout": 30,
    }
    proposals = root / ".local/two-site-refactor/proposals"
    worker_result = proposals / "qresume-ready-worker-0/result.json"
    reviewer_result = proposals / "qresume-ready-reviewer-0/result.json"
    reviewer_result.parent.mkdir(parents=True)
    proof = {
        "state": "BINDINGS_VALID", "head": request["source_head"],
        "gemini_worker_run_id": worker_result.parent.name,
        "gemini_reviewer_run_id": reviewer_result.parent.name,
        "proposal_sha256": "e" * 64, "changed_paths": ["tests/example.py"],
        "worker_result": str(worker_result), "reviewer_result": str(reviewer_result),
        "test_selectors": ["tests/test_example.py"],
    }
    receipt_path = root / (
        ".local/two-site-refactor/pipeline/qresume-ready/pre-review-tests-0.json"
    )
    receipt_path.parent.mkdir(parents=True)
    receipt = {"state": "PASSED", "selectors": proof["test_selectors"],
               "proposal_sha256": proof["proposal_sha256"]}
    receipt_path.write_bytes(pipeline.serialize_json(receipt))
    receipt_path.chmod(0o600)
    receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    reviewer_status = reviewer_result.parent / "status.json"
    reviewer_status.write_text(json.dumps(
        {"input_test_evidence_sha256": receipt_sha}), encoding="utf-8")
    reviewer_status.chmod(0o600)
    ready = {
        "state": "READY_FOR_CODEX", "task_id": request["task_id"],
        "source_head": request["source_head"],
        "final_worker_run_id": proof["gemini_worker_run_id"],
        "final_reviewer_run_id": proof["gemini_reviewer_run_id"],
        "proposal_sha256": proof["proposal_sha256"],
        "changed_paths": proof["changed_paths"],
        "proposal_path": worker_result.relative_to(root).as_posix(),
        "reviewer_path": reviewer_result.relative_to(root).as_posix(),
        "correction_count": 0, "blockers": [],
        "pre_review_test_path": receipt_path.relative_to(root).as_posix(),
        "pre_review_test_sha256": receipt_sha, "pre_review_test_state": "PASSED",
        "semantic_task_fingerprint": request["semantic_task_fingerprint"],
    }
    response = {
        "schema_version": "1.0", "request_id": request["request_id"],
        "run_prefix": request["run_prefix"], "source_head": request["source_head"],
        "state": "COMPLETED", "exit_code": 0, "stdout": "", "stderr": "",
        "finished_at": 1234.0,
    }
    record = {"request": deepcopy(request), "response": response}
    queue = root / (
        ".local/two-site-refactor/provider-broker/responses/"
        + request["request_id"] + ".json"
    )
    queue.parent.mkdir(parents=True)
    calls: list[Any] = []

    def sync() -> None:
        response["stdout"] = json.dumps(ready, separators=(",", ":"))
        queue.write_text(json.dumps(response), encoding="utf-8")
        queue.chmod(0o600)

    sync()

    def admit(actual_root: Any, actual_request: Any) -> Any:
        assert actual_root == root
        assert actual_request == request
        calls.append("admit")
        return deepcopy(request), task_path, object()

    def wait(actual_root: Any, actual_request: Any, identity: Any, timeout: Any) -> Any:
        assert actual_root == root and actual_request == request
        assert identity == (123, 456.0) and timeout == request["timeout"]
        calls.append("wait")
        return deepcopy(response)

    def read_terminal(actual_root: Any, request_id: Any, **kwargs: Any) -> Any:
        assert actual_root == root and request_id == request["request_id"]
        assert kwargs == {"expected_request": request}
        calls.append("terminal")
        return deepcopy(record)

    def review(*args: Any, **kwargs: Any) -> Any:
        assert args == (root, task_path, ready["final_worker_run_id"],
                        ready["final_reviewer_run_id"], ready["proposal_sha256"])
        assert kwargs == {"applied": False}
        calls.append("review")
        return deepcopy(proof)

    def test_receipt(value: Any, proposal_sha: Any, *, require_passed: Any) -> Any:
        assert value == receipt and proposal_sha == proof["proposal_sha256"]
        assert require_passed is True
        calls.append("native")
        return deepcopy(receipt)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("wait-only handoff attempted publication or Provider execution")

    monkeypatch.setattr(broker, "_STOP", False)
    monkeypatch.setattr(admission, "validate_quota_continuation_request", admit)
    monkeypatch.setattr(terminal, "read_terminal_continuation_record", read_terminal)
    monkeypatch.setattr(broker, "_wait_existing_response", wait, raising=False)
    monkeypatch.setattr(broker, "read_terminal_quota_record",
                        lambda *a: {"request": {"run_prefix": "original"}})
    monkeypatch.setattr(tools, "review", review)
    monkeypatch.setattr(pipeline, "validate_test_receipt", test_receipt)
    monkeypatch.setattr(broker, "_submit_request", forbidden)
    monkeypatch.setattr(broker, "publish_cycle_reservation", forbidden)
    monkeypatch.setattr(broker, "execute_pipeline", forbidden)
    return dict(root=root, request=request, ready=ready, response=response,
                record=record, queue=queue, task=task_path, proof=proof,
                receipt=receipt_path, reviewer_status=reviewer_status, calls=calls,
                sync=sync, wait=wait, admit=admit, review=review, context=context,
                broker=broker, admission=admission, terminal=terminal, tools=tools)


def _read_ready(env: dict[str, Any]) -> dict[str, Any]:
    return env["context"].wait_for_known_reset_resume_ready_locked(
        env["root"], env["request"], expected_broker_identity=(123, 456.0)
    )


def test_resume_ready_wait_only_exact_custody_and_detached_result(
    ready_handoff: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    import hashlib

    env = ready_handoff
    before = {p: (p.stat().st_ino, p.read_bytes())
              for p in env["root"].rglob("*") if p.is_file()}
    result = _read_ready(env)
    assert set(result) == {"request", "response", "ready", "terminal_receipt_sha256"}
    assert result["ready"] == env["ready"] and result["request"] == env["request"]
    expected = json.dumps(env["record"], sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    assert result["terminal_receipt_sha256"] == hashlib.sha256(expected).hexdigest()
    assert env["calls"] == ["admit", "wait", "terminal", "review", "native",
                            "admit", "terminal"]
    assert before == {p: (p.stat().st_ino, p.read_bytes())
                      for p in env["root"].rglob("*") if p.is_file()}
    assert capsys.readouterr() == ("", "")
    result["ready"]["blockers"].append("detached")
    result["request"]["timeout"] = 99
    assert env["ready"]["blockers"] == [] and env["request"]["timeout"] == 30


@pytest.mark.parametrize("field,value", [
    ("state", "TASK_BLOCKED"), ("blockers", ["blocked"]),
    ("task_id", "different"), ("source_head", "f" * 40),
    ("semantic_task_fingerprint", "f" * 64), ("proposal_path", "other.json"),
    ("reviewer_path", "other.json"), ("changed_paths", []),
    ("correction_count", True), ("pre_review_test_state", "FAILED"),
    ("pre_review_test_sha256", "f" * 64), ("unknown", "not admitted"),
])
def test_resume_ready_rejects_inexact_inner_handoff(
    ready_handoff: dict[str, Any], field: str, value: Any
) -> None:
    ready_handoff["ready"][field] = value
    ready_handoff["sync"]()
    with pytest.raises(ResumeContextError):
        _read_ready(ready_handoff)


def test_resume_ready_queue_terminal_type_mismatch(
    ready_handoff: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from copy import deepcopy

    env = ready_handoff
    different = deepcopy(env["record"])
    different["response"]["exit_code"] = False
    monkeypatch.setattr(env["terminal"], "read_terminal_continuation_record",
                        lambda *a, **k: different)
    with pytest.raises(ResumeContextError, match="queue/terminal mismatch"):
        _read_ready(env)


@pytest.mark.parametrize("fault", ["task", "terminal", "queue", "request",
                                    "reviewer_native", "selectors"])
def test_resume_ready_rejects_drift_after_real_chain_boundary(
    ready_handoff: dict[str, Any], monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    env = ready_handoff
    original = env["review"]
    original_admit = env["admit"]
    request_drift = False

    def admitted_at_observation(*args: Any) -> Any:
        admitted, task_path, scope = original_admit(*args)
        if request_drift:
            admitted["timeout"] += 1
        return admitted, task_path, scope

    # Bind before the helper captures its local import. Later review changes the
    # observation, not the admission-module attribute that was already captured.
    monkeypatch.setattr(env["admission"], "validate_quota_continuation_request",
                        admitted_at_observation)

    def drift(*args: Any, **kwargs: Any) -> Any:
        nonlocal request_drift
        proof = original(*args, **kwargs)
        if fault == "task":
            env["task"].write_bytes(b"changed")
        elif fault == "terminal":
            env["record"]["response"] = dict(env["response"], finished_at=999)
        elif fault == "queue":
            env["queue"].write_text(json.dumps(dict(env["response"], finished_at=999)))
        elif fault == "request":
            request_drift = True
        elif fault == "reviewer_native":
            env["reviewer_status"].write_text(json.dumps(
                {"input_test_evidence_sha256": "0" * 64}))
        elif fault == "selectors":
            proof["test_selectors"] = ["tests/test_different.py"]
        return proof

    monkeypatch.setattr(env["tools"], "review", drift)
    with pytest.raises(ResumeContextError) as exc_info:
        _read_ready(env)
    if fault == "request":
        assert str(exc_info.value) == "resume READY request drift"
        assert env["calls"].count("admit") == 2



def test_resume_ready_verifier_reuses_original_native_receipt_without_worker_replay(
    ready_handoff: dict[str, Any]
) -> None:
    import hashlib

    env = ready_handoff
    env["request"]["role"] = "verifier"
    env["record"]["request"]["role"] = "verifier"
    destination = (env["root"] /
                   ".local/two-site-refactor/pipeline/original/pre-review-tests-0.json")
    destination.parent.mkdir(parents=True)
    env["receipt"].rename(destination)
    env["receipt"] = destination
    env["ready"]["pre_review_test_path"] = destination.relative_to(env["root"]).as_posix()
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == env["ready"]["pre_review_test_sha256"]
    env["sync"]()
    assert _read_ready(env)["ready"] == env["ready"]
    assert env["calls"].count("wait") == 1


@pytest.mark.parametrize("identity", [None, (True, 1.0), (123, True),
                                        (123, float("nan")), (123, float("inf"))])
def test_resume_ready_requires_actual_finite_broker_identity(
    ready_handoff: dict[str, Any], identity: Any
) -> None:
    env = ready_handoff
    with pytest.raises(ResumeContextError):
        env["context"].wait_for_known_reset_resume_ready_locked(
            env["root"], env["request"], expected_broker_identity=identity)
    assert env["calls"] == []



@pytest.mark.parametrize("where", ["root", "autopilot", "broker_flag", "after_wait"])
def test_resume_ready_stop_is_fail_closed(
    ready_handoff: dict[str, Any], monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    env = ready_handoff

    def stop_file() -> None:
        path = (env["root"] / "STOP" if where == "root" else
                env["root"] / ".local/two-site-refactor/autopilot/STOP")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("STOP\n")

    if where == "broker_flag":
        monkeypatch.setattr(env["broker"], "_STOP", None)
    elif where == "after_wait":
        def stopped(*args: Any) -> Any:
            response = env["wait"](*args)
            stop_file()
            return response
        monkeypatch.setattr(env["broker"], "_wait_existing_response", stopped)
    else:
        stop_file()
    with pytest.raises(ResumeContextError):
        _read_ready(env)
    assert "review" not in env["calls"]
    assert env["calls"].count("wait") == (1 if where == "after_wait" else 0)


def test_resume_ready_wait_timeout_preserves_cause_without_retry(
    ready_handoff: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = ready_handoff
    failure = TimeoutError("controlled deadline")
    count = 0

    def timeout(*args: Any) -> Any:
        nonlocal count
        count += 1
        raise failure

    monkeypatch.setattr(env["broker"], "_wait_existing_response", timeout)
    with pytest.raises(ResumeContextError) as exc_info:
        _read_ready(env)
    assert exc_info.value.__cause__ is failure
    assert count == 1 and "review" not in env["calls"]


@pytest.mark.parametrize("fault", ["symlink", "oversize", "missing"])
def test_resume_ready_preserves_bounded_native_receipt_reader(
    ready_handoff: dict[str, Any], fault: str
) -> None:
    env = ready_handoff
    if fault == "symlink":
        target = env["root"] / "other.json"
        target.write_bytes(env["receipt"].read_bytes())
        env["receipt"].unlink()
        env["receipt"].symlink_to(target)
    elif fault == "oversize":
        env["receipt"].write_bytes(b"x" * (160_000 + 1))
    else:
        env["receipt"].unlink()
    with pytest.raises(ResumeContextError):
        _read_ready(env)
