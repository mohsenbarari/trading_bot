"""Tests for proposal runner pre-review test-evidence transport."""
from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path
import sys
from unittest import mock

import pytest

try:
    from scripts import two_site_proposal_runner
except ImportError:
    import two_site_proposal_runner


@pytest.fixture
def runner_env(tmp_path, monkeypatch):
    root = (tmp_path / "repo").resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "docs/refactor/two-server/templates").mkdir(parents=True, exist_ok=True)
    (root / "docs/refactor/two-server/templates/worker-model-routing.json").write_bytes(
        (Path(__file__).resolve().parents[1] / "docs/refactor/two-server/templates/worker-model-routing.json").read_bytes()
    )
    (root / "docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md").write_text("# Task\n")
    (root / "sample.py").write_text("def run():\n    return False\n")

    task = {
        "task_id": "task-evidence-01", "goal": "Verify test evidence transport",
        "context_files": ["sample.py"], "allowed_files": ["sample.py"],
        "requirements": ["Requirement 1"], "test_selectors": ["tests/test_sample.py"],
    }
    task_path = root / "task.json"
    task_path.write_text(json.dumps(task))

    src_sha = hashlib.sha256(b"def run():\n    return False\n").hexdigest()
    sources = {"sample.py": {"sha256": src_sha, "content": "def run():\n    return False\n"}}
    worker_proposal = {
        "status": "PROPOSAL_READY", "summary": "Fix return value", "covered_requirements": [1],
        "findings": [], "changes": [{"path": "sample.py", "original_sha256": src_sha, "content": "def run():\n    return True\n"}],
    }
    proposal_text = json.dumps(worker_proposal, indent=2)
    proposal_sha = hashlib.sha256(proposal_text.encode("utf-8")).hexdigest()

    worker_dir = root / "worker_run_01"
    worker_dir.mkdir(parents=True, exist_ok=True)
    proposal_path = worker_dir / "result.json"
    proposal_path.write_text(proposal_text)
    (worker_dir / "task.json").write_text(json.dumps({"task": task, "source_head": "0" * 40, "sources": sources}))
    (worker_dir / "status.json").write_text(json.dumps({
        "role": "worker", "state": "PROPOSAL_READY", "result_sha256": proposal_sha,
        "run_id": "worker-run-01", "conversation_id": "conv-worker-01",
    }))

    receipt = {
        "state": "PASSED", "proposal_sha256": proposal_sha, "selectors": ["tests/test_sample.py"],
        "unsupported_specs": [], "returncode": 0, "stdout_tail": "1 passed in 0.01s",
        "stderr_tail": "", "snapshot_files": 1, "snapshot_bytes": 128, "isolation": "sandbox",
    }
    evidence_path = root / "receipt.json"
    evidence_path.write_text(json.dumps(receipt))

    monkeypatch.setattr(two_site_proposal_runner, "ROOT", root)
    monkeypatch.setattr(two_site_proposal_runner, "validate_runtime_contract",
                        lambda r: {"model": two_site_proposal_runner.MODEL, "effort": two_site_proposal_runner.EFFORT})
    monkeypatch.setattr(two_site_proposal_runner, "git",
                        lambda r, *args: "0" * 40 if args and args[0] == "rev-parse" else "")

    @contextlib.contextmanager
    def fake_proposal_cli_context(r, cli=None):
        proposals_dir = r / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        yield proposals_dir, Path("/fake/cli"), {"path": "/fake/cli", "sha256": "0" * 64}

    monkeypatch.setattr(two_site_proposal_runner, "proposal_cli_context", fake_proposal_cli_context)
    monkeypatch.setattr(two_site_proposal_runner, "cli_identity",
                        lambda p: {"path": str(p), "version": two_site_proposal_runner.EXPECTED_CLI_VERSION, "sha256": "0" * 64})

    mock_process = mock.MagicMock(pid=12345, returncode=0)
    mock_process.__enter__.return_value = mock_process
    mock_process.__exit__.return_value = None
    mock_popen = mock.MagicMock(return_value=mock_process)
    monkeypatch.setattr(two_site_proposal_runner.subprocess, "Popen", mock_popen)

    stream_content = "\n".join([
        json.dumps({"event": "init", "conversation_id": "conv-verifier-01", "init": {"model": two_site_proposal_runner.MODEL}}),
        json.dumps({"event": "result", "conversation_id": "conv-verifier-01", "result": {
            "status": "SUCCESS", "conversation_id": "conv-verifier-01",
            "structured_output": {"status": "VERIFIED", "summary": "Verified proposal meets all requirements",
                                  "covered_requirements": [1], "findings": [], "changes": []},
        }}),
    ])

    def fake_capture(proc, in_bytes, stdout_path, stderr_path, timeout, max_output=2_000_000):
        stdout_path.write_text(stream_content)
        stderr_path.write_text("")

    monkeypatch.setattr(two_site_proposal_runner, "capture_bounded_process", fake_capture)

    return {
        "root": root,
        "task_path": task_path,
        "proposal_path": proposal_path,
        "proposal_sha": proposal_sha,
        "receipt": receipt,
        "evidence_path": evidence_path,
        "mock_popen": mock_popen,
    }


def test_verifier_test_evidence_transport_success(runner_env):
    root = runner_env["root"]
    evidence_path = runner_env["evidence_path"]
    evidence_bytes = evidence_path.read_bytes()
    expected_evidence_sha = hashlib.sha256(evidence_bytes).hexdigest()

    out = two_site_proposal_runner.run(
        root,
        runner_env["task_path"],
        "verifier",
        "verifier-run-01",
        300,
        proposal=runner_env["proposal_path"],
        test_evidence=evidence_path,
    )
    runner_env["mock_popen"].assert_called_once()

    prompt_text = (out / "prompt.txt").read_text()
    prompt_context = json.loads(prompt_text.split("\nCONTEXT_JSON:\n")[1])
    assert prompt_context["pre_review_test_evidence"] == runner_env["receipt"]

    task_json = json.loads((out / "task.json").read_text())
    assert task_json["test_evidence"] == runner_env["receipt"]

    status = json.loads((out / "status.json").read_text())
    assert status["input_test_evidence_sha256"] == expected_evidence_sha
    assert status["input_worker_run_id"] == "worker-run-01"
    assert status["input_worker_conversation_id"] == "conv-worker-01"
    assert status["input_proposal_sha256"] == runner_env["proposal_sha"]


def test_invalid_evidence_and_worker_fail_before_popen(runner_env):
    root, tpath, ppath = runner_env["root"], runner_env["task_path"], runner_env["proposal_path"]
    mock_popen = runner_env["mock_popen"]
    bad_cases = [
        ("f.json", dict(runner_env["receipt"], state="FAILED", returncode=1)),
        ("m.json", {"state": "PASSED"}),
        ("s.json", dict(runner_env["receipt"], proposal_sha256="1" * 64)),
    ]
    for filename, payload in bad_cases:
        bad_file = root / filename
        bad_file.write_text(json.dumps(payload))
        mock_popen.reset_mock()
        with pytest.raises(Exception):
            two_site_proposal_runner.run(
                root, tpath, "verifier", f"run-{filename}", 300,
                proposal=ppath, test_evidence=bad_file,
            )
        mock_popen.assert_not_called()

    mock_popen.reset_mock()
    with pytest.raises(two_site_proposal_runner.ProposalError, match="test evidence is only allowed for verifier"):
        two_site_proposal_runner.run(
            root, tpath, "worker", "run-worker-ev", 300,
            test_evidence=runner_env["evidence_path"],
        )
    mock_popen.assert_not_called()


def test_verifier_without_evidence_compatibility(runner_env):
    root = runner_env["root"]
    runner_env["mock_popen"].reset_mock()
    out = two_site_proposal_runner.run(
        root,
        runner_env["task_path"],
        "verifier",
        "verifier-run-no-evidence",
        300,
        proposal=runner_env["proposal_path"],
        test_evidence=None,
    )
    runner_env["mock_popen"].assert_called_once()

    prompt_context = json.loads((out / "prompt.txt").read_text().split("\nCONTEXT_JSON:\n")[1])
    assert "pre_review_test_evidence" not in prompt_context

    task_json = json.loads((out / "task.json").read_text())
    assert "test_evidence" not in task_json

    status = json.loads((out / "status.json").read_text())
    assert "input_test_evidence_sha256" not in status
    assert status["input_worker_run_id"] == "worker-run-01"
    assert status["input_worker_conversation_id"] == "conv-worker-01"
    assert status["input_proposal_sha256"] == runner_env["proposal_sha"]


@pytest.mark.parametrize("argv,reason_sub", [
    (["check", "--test-evidence"], "check mode does not accept"),
    (["worker", "--run-id", "w-gate", "--test-evidence"], "test evidence is only allowed for verifier"),
])
def test_cli_argument_gates_reject_test_evidence(runner_env, monkeypatch, capsys, argv, reason_sub):
    mock_popen = runner_env["mock_popen"]
    mock_popen.reset_mock()
    cmd = ["two_site_proposal_runner.py", *argv[:-1], "--task", str(runner_env["task_path"]),
           "--root", str(runner_env["root"]), argv[-1], str(runner_env["evidence_path"])]
    monkeypatch.setattr(sys, "argv", cmd)
    exit_code = two_site_proposal_runner.main()
    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["state"] == "BLOCKED"
    assert reason_sub in output["reason"]
    mock_popen.assert_not_called()
