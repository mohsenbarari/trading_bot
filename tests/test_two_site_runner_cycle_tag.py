#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
from unittest import mock

import pytest

try:
    from scripts.two_site_proposal_runner import (
        AUTOPILOT_RUN_ID_ENV,
        MAX_AUTOPILOT_RUN_ID_LENGTH,
        ProposalError,
        RetryableProviderSessionError,
        run,
        run_standalone,
        validate_autopilot_run_id,
    )
except ImportError:
    from two_site_proposal_runner import (
        AUTOPILOT_RUN_ID_ENV,
        MAX_AUTOPILOT_RUN_ID_LENGTH,
        ProposalError,
        RetryableProviderSessionError,
        run,
        run_standalone,
        validate_autopilot_run_id,
    )

VALID_CYCLE_TAG = "cycle-0001-20260913T120000Z-12345"
SAMPLE_SHA = "a" * 64


def test_validate_autopilot_run_id_valid():
    assert validate_autopilot_run_id(None) is None
    assert validate_autopilot_run_id(VALID_CYCLE_TAG) == VALID_CYCLE_TAG
    assert validate_autopilot_run_id("cycle-0042-20260913T235959Z-1") == "cycle-0042-20260913T235959Z-1"
    assert (
        validate_autopilot_run_id("cycle-10000-20260913T000000Z-99999999")
        == "cycle-10000-20260913T000000Z-99999999"
    )


def test_validate_autopilot_run_id_malformed():
    with pytest.raises(ProposalError, match="empty or whitespace"):
        validate_autopilot_run_id("")
    with pytest.raises(ProposalError, match="empty or whitespace"):
        validate_autopilot_run_id("   ")
    with pytest.raises(ProposalError, match="canonical format"):
        validate_autopilot_run_id("not-a-cycle")
    with pytest.raises(ProposalError, match="canonical format"):
        validate_autopilot_run_id("cycle-01-20260913T120000Z-123")
    with pytest.raises(ProposalError, match="canonical format"):
        validate_autopilot_run_id("cycle-0001-20260913-123")
    with pytest.raises(ProposalError, match="canonical format"):
        validate_autopilot_run_id("cycle-0001-20260913T120000Z-")
    with pytest.raises(ProposalError, match="canonical format"):
        validate_autopilot_run_id("cycle-0001-20260913T120000Z-abc")
    with pytest.raises(ProposalError, match="canonical format"):
        validate_autopilot_run_id("cycle-0001-20260913T120000Z-12345\n")
    overlong = "cycle-0001-20260913T120000Z-" + ("9" * 40)
    with pytest.raises(ProposalError, match="exceeds length limit"):
        validate_autopilot_run_id(overlong)


def test_malformed_rejection_before_evidence_or_provider(monkeypatch, tmp_path):
    monkeypatch.setenv(AUTOPILOT_RUN_ID_ENV, "cycle-invalid-tag")
    task_file = tmp_path / "task.json"
    task_file.write_text("{}", encoding="utf-8")

    with mock.patch("scripts.two_site_proposal_runner.proposal_cli_context") as mock_cli_ctx, \
         mock.patch("scripts.two_site_proposal_runner.cli_identity") as mock_cli_id:
        with pytest.raises(ProposalError, match="canonical format"):
            run_standalone(
                root=tmp_path,
                task_path=task_file,
                role="worker",
                base_run_id="run-malformed",
                timeout=30,
            )

        mock_cli_ctx.assert_not_called()
        mock_cli_id.assert_not_called()

    evidence_dir = tmp_path / ".local/two-site-refactor/proposals/run-malformed"
    assert not evidence_dir.exists()


@contextlib.contextmanager
def _mock_runner_execution(
    root: Path,
    role: str = "worker",
    fail_in_execution: bool = False,
    retryable_failure: bool = False,
):
    task = {
        "task_id": "test-task",
        "goal": "sample goal",
        "context_files": ["sample.py"],
        "allowed_files": ["sample.py"],
        "requirements": ["r1"],
        "test_selectors": ["pytest -q"],
    }
    sources = {
        "sample.py": {"sha256": SAMPLE_SHA, "content": "val = 1\n"},
    }

    proposals_root = root / ".local/two-site-refactor/proposals"
    proposals_root.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def fake_proposal_cli_context(r, cli=None):
        yield proposals_root, root / "fake_cli", {
            "path": str(root / "fake_cli"),
            "sha256": "0" * 64,
            "version": "1.0.0",
        }

    def fake_git(r, *args):
        if args and args[0] == "rev-parse":
            return "0" * 40
        return ""

    def fake_bounded_file_text(r, path, limit, label):
        path_obj = Path(path)
        if "prompt template" in label:
            return "PROMPT TEMPLATE"
        return path_obj.read_text(encoding="utf-8")

    def fake_capture(process, input_bytes, stdout_path, stderr_path, timeout, max_output=2_000_000):
        if fail_in_execution:
            raise ProposalError("simulated provider failure")
        if retryable_failure:
            raise RetryableProviderSessionError("CLI did not complete one successful session")

        model_name = "gemini-3.8-flash-medium" if role == "worker" else "gemini-3.8-flash-high"
        init_event = {
            "event": "init",
            "conversation_id": "session-xyz",
            "init": {"model": model_name},
        }

        current_content = "val = 1\n"
        current_sha = SAMPLE_SHA
        if input_bytes:
            try:
                input_json = json.loads(input_bytes.decode("utf-8").strip())
                prompt_text = input_json.get("message", {}).get("content", "")
                if "CONTEXT_JSON:\n" in prompt_text:
                    context_data = json.loads(prompt_text.split("CONTEXT_JSON:\n", 1)[1])
                    sample_info = context_data.get("sources", {}).get("sample.py")
                    if sample_info and sample_info.get("content") is not None:
                        current_content = sample_info["content"]
                        current_sha = sample_info["sha256"]
            except Exception:
                pass

        new_content = "val = 2\n" if current_content == "val = 1\n" else "val = 3\n"

        res_payload = {
            "status": "PROPOSAL_READY" if role == "worker" else "VERIFIED",
            "summary": "verified change",
            "covered_requirements": [1],
            "findings": [],
            "changes": [{
                "path": "sample.py",
                "original_sha256": current_sha,
                "content": None,
                "replacements": [{"old": current_content, "new": new_content}],
            }] if role == "worker" else [],
        }
        result_event = {
            "event": "result",
            "conversation_id": "session-xyz",
            "result": {
                "status": "SUCCESS",
                "conversation_id": "session-xyz",
                "structured_output": res_payload,
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        }
        stdout_path.write_bytes(
            (json.dumps(init_event) + "\n" + json.dumps(result_event) + "\n").encode("utf-8")
        )
        stderr_path.write_bytes(b"")

    mock_process = mock.MagicMock()
    mock_process.pid = 4321
    mock_process.returncode = 0
    mock_process.__enter__.return_value = mock_process
    mock_process.__exit__.return_value = None

    with mock.patch("scripts.two_site_proposal_runner.validate_runtime_contract", return_value={}), \
         mock.patch("scripts.two_site_proposal_runner.load_task", return_value=(task, sources)), \
         mock.patch("scripts.two_site_proposal_runner.task_preparation_notes", return_value={}), \
         mock.patch("scripts.two_site_proposal_runner.validate_task_readiness", return_value={}), \
         mock.patch("scripts.two_site_proposal_runner.git", side_effect=fake_git), \
         mock.patch("scripts.two_site_proposal_runner.proposal_cli_context", side_effect=fake_proposal_cli_context), \
         mock.patch("scripts.two_site_proposal_runner.cli_identity", return_value={"path": "cli", "version": "1.0.0", "sha256": "0" * 64}), \
         mock.patch("scripts.two_site_proposal_runner.bounded_file_text", side_effect=fake_bounded_file_text), \
         mock.patch("scripts.two_site_proposal_runner.capture_bounded_process", side_effect=fake_capture), \
         mock.patch("subprocess.Popen", return_value=mock_process):
        yield


def test_absence_for_manual_runs(monkeypatch, tmp_path):
    monkeypatch.delenv(AUTOPILOT_RUN_ID_ENV, raising=False)
    task_file = tmp_path / "task.json"
    task_file.write_text("{}", encoding="utf-8")

    with _mock_runner_execution(tmp_path, role="worker"):
        out = run(
            root=tmp_path,
            task_path=task_file,
            role="worker",
            run_id="worker-manual-1",
            timeout=30,
        )

    status_data = json.loads((out / "status.json").read_text(encoding="utf-8"))
    assert status_data["state"] == "PROPOSAL_READY"
    assert "controller_run_id" not in status_data


def test_persistence_on_success_and_absence_from_prompt_and_task(monkeypatch, tmp_path):
    monkeypatch.setenv(AUTOPILOT_RUN_ID_ENV, VALID_CYCLE_TAG)
    task_file = tmp_path / "task.json"
    task_file.write_text("{}", encoding="utf-8")

    with _mock_runner_execution(tmp_path, role="worker"):
        out = run(
            root=tmp_path,
            task_path=task_file,
            role="worker",
            run_id="worker-auto-1",
            timeout=30,
        )

    status_data = json.loads((out / "status.json").read_text(encoding="utf-8"))
    assert status_data["state"] == "PROPOSAL_READY"
    assert status_data.get("controller_run_id") == VALID_CYCLE_TAG

    prompt_content = (out / "prompt.txt").read_text(encoding="utf-8")
    assert VALID_CYCLE_TAG not in prompt_content
    assert "controller_run_id" not in prompt_content

    task_content = (out / "task.json").read_text(encoding="utf-8")
    assert VALID_CYCLE_TAG not in task_content
    assert "controller_run_id" not in task_content

    result_content = (out / "result.json").read_text(encoding="utf-8")
    assert "controller_run_id" not in result_content


def test_persistence_on_exception_blocked(monkeypatch, tmp_path):
    monkeypatch.setenv(AUTOPILOT_RUN_ID_ENV, VALID_CYCLE_TAG)
    task_file = tmp_path / "task.json"
    task_file.write_text("{}", encoding="utf-8")

    with _mock_runner_execution(tmp_path, role="worker", fail_in_execution=True):
        with pytest.raises(ProposalError, match="simulated provider failure"):
            run(
                root=tmp_path,
                task_path=task_file,
                role="worker",
                run_id="worker-fail-1",
                timeout=30,
            )

    out = tmp_path / ".local/two-site-refactor/proposals/worker-fail-1"
    status_data = json.loads((out / "status.json").read_text(encoding="utf-8"))
    assert status_data["state"] == "BLOCKED"
    assert status_data.get("controller_run_id") == VALID_CYCLE_TAG
    assert "simulated provider failure" in status_data.get("error", "")


def test_inheritance_across_worker_and_verifier(monkeypatch, tmp_path):
    monkeypatch.setenv(AUTOPILOT_RUN_ID_ENV, VALID_CYCLE_TAG)
    task_file = tmp_path / "task.json"
    task_file.write_text("{}", encoding="utf-8")

    with _mock_runner_execution(tmp_path, role="worker"):
        worker_out = run(
            root=tmp_path,
            task_path=task_file,
            role="worker",
            run_id="worker-chain-1",
            timeout=30,
        )

    worker_status = json.loads((worker_out / "status.json").read_text(encoding="utf-8"))
    assert worker_status.get("controller_run_id") == VALID_CYCLE_TAG

    proposal_file = worker_out / "result.json"
    candidate = json.loads(proposal_file.read_text(encoding="utf-8"))
    prop_sha = hashlib.sha256(proposal_file.read_bytes()).hexdigest()
    prior_worker_status = {
        "run_id": "worker-chain-1",
        "conversation_id": "worker-session-1",
    }

    feedback_file = tmp_path / "feedback.json"
    feedback_file.write_text(json.dumps({
        "reviewer": "test-gate",
        "reviewed_proposal_sha256": prop_sha,
        "findings": ["needs improvement"],
        "test_evidence": ["test failed"],
    }), encoding="utf-8")

    with _mock_runner_execution(tmp_path, role="worker"), \
         mock.patch("scripts.two_site_proposal_runner.load_prior_worker", return_value=(candidate, prior_worker_status, prop_sha)), \
         mock.patch("scripts.two_site_proposal_runner.rebase_correction_result", return_value=candidate):
        correction_out = run(
            root=tmp_path,
            task_path=task_file,
            role="worker",
            run_id="correction-chain-1",
            timeout=30,
            proposal=proposal_file,
            feedback=feedback_file,
        )

    correction_status = json.loads((correction_out / "status.json").read_text(encoding="utf-8"))
    assert correction_status.get("controller_run_id") == VALID_CYCLE_TAG
    assert correction_status["state"] == "PROPOSAL_READY"

    with _mock_runner_execution(tmp_path, role="verifier"), \
         mock.patch("scripts.two_site_proposal_runner.load_prior_worker", return_value=(candidate, prior_worker_status, prop_sha)):
        verifier_out = run(
            root=tmp_path,
            task_path=task_file,
            role="verifier",
            run_id="verifier-chain-1",
            timeout=30,
            proposal=proposal_file,
        )

    verifier_status = json.loads((verifier_out / "status.json").read_text(encoding="utf-8"))
    assert verifier_status.get("controller_run_id") == VALID_CYCLE_TAG
    assert verifier_status["state"] == "VERIFIED"


def test_retry_preserves_controller_run_id(monkeypatch, tmp_path):
    monkeypatch.setenv(AUTOPILOT_RUN_ID_ENV, VALID_CYCLE_TAG)
    task_file = tmp_path / "task.json"
    task_file.write_text("{}", encoding="utf-8")

    attempts_called = []
    original_run = run

    def alternating_run(*args, **kwargs):
        run_id = kwargs.get("run_id") if "run_id" in kwargs else args[3]
        attempts_called.append(run_id)
        if len(attempts_called) == 1:
            with _mock_runner_execution(tmp_path, role="worker", retryable_failure=True):
                return original_run(*args, **kwargs)
        with _mock_runner_execution(tmp_path, role="worker", retryable_failure=False):
            return original_run(*args, **kwargs)

    with mock.patch("scripts.two_site_proposal_runner.run", side_effect=alternating_run):
        out = run_standalone(
            root=tmp_path,
            task_path=task_file,
            role="worker",
            base_run_id="retry-run-1",
            timeout=30,
        )

    assert len(attempts_called) == 2
    attempt1_dir = tmp_path / ".local/two-site-refactor/proposals/retry-run-1"
    attempt1_status = json.loads((attempt1_dir / "status.json").read_text(encoding="utf-8"))
    assert attempt1_status["state"] == "BLOCKED"
    assert attempt1_status.get("controller_run_id") == VALID_CYCLE_TAG

    attempt2_status = json.loads((out / "status.json").read_text(encoding="utf-8"))
    assert attempt2_status["state"] == "PROPOSAL_READY"
    assert attempt2_status.get("controller_run_id") == VALID_CYCLE_TAG


def test_maintenance_provenance_is_distinct_from_supervisor_cycle() -> None:
    from scripts import two_site_proposal_runner as runner

    maintenance = "maint-20260923T120000Z-1234567890abcdef"
    assert runner.controller_run_id_from_environment(
        {runner.MAINTENANCE_RUN_ID_ENV: maintenance}
    ) == maintenance
    assert runner.controller_run_id_from_environment(
        {runner.AUTOPILOT_RUN_ID_ENV: VALID_CYCLE_TAG}
    ) == VALID_CYCLE_TAG
    assert runner.controller_run_id_from_environment({}) is None
    with pytest.raises(ProposalError, match="ambiguous"):
        runner.controller_run_id_from_environment({
            runner.AUTOPILOT_RUN_ID_ENV: VALID_CYCLE_TAG,
            runner.MAINTENANCE_RUN_ID_ENV: maintenance,
        })
    with pytest.raises(ProposalError, match="invalid maintenance"):
        runner.controller_run_id_from_environment({
            runner.MAINTENANCE_RUN_ID_ENV: "cycle-0001-20260923T120000Z-1",
        })
    with pytest.raises(ProposalError, match="canonical format"):
        runner.validate_autopilot_run_id(maintenance)
