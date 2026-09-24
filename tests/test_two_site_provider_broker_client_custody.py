from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest import mock

import pytest

from scripts import two_site_provider_broker as broker
from scripts.two_site_assignment_scope import AssignmentScope


@pytest.fixture
def broker_repo(tmp_path_factory) -> tuple[Path, Path, AssignmentScope, dict[str, str]]:
    root = tmp_path_factory.mktemp("broker") / "integration"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    (root / ".gitignore").write_text(".local/\n", encoding="utf-8")
    (root / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    pipeline = root / broker.PIPELINE_REL
    pipeline.parent.mkdir()
    pipeline.write_text("raise SystemExit(99)\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", ".gitignore", "source.py", str(broker.PIPELINE_REL)], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    subprocess.run(["git", "-C", str(root), "branch", "-M", broker.BRANCH], check=True)

    task = root / broker.TASK_PREFIX / "task.json"
    task.parent.mkdir(parents=True)
    task.write_text(json.dumps({
        "task_id": "BROKER-CUSTODY-TEST",
        "goal": "WA-BROKER-CUSTODY: bounded client custody tests.",
        "context_files": ["source.py"],
        "allowed_files": ["tests/test_new.py"],
        "requirements": ["Cover client custody response boundaries."],
        "test_selectors": ["tests/test_new.py"],
    }), encoding="utf-8")
    scope = AssignmentScope(
        assignment_id="WA-BROKER-TEST",
        stage_id="P1-00b",
        allowed_paths=("tests/**",),
        forbidden_paths=("scripts/**",),
        expires_at="2099-01-01T00:00:00Z",
        sha256="a" * 64,
    )
    environment = {
        "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED": "1",
        "TWO_SITE_ASSIGNMENT_ID": scope.assignment_id,
        "TWO_SITE_ASSIGNMENT_SHA256": scope.sha256,
        "TWO_SITE_AUTOPILOT_RUN_ID": "cycle-0001-20990101T000000Z-123",
    }
    return root, task, scope, environment


def _prepare_client_context(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]],
    run_prefix: str = "custody-run",
) -> tuple[Path, dict, Path, Path]:
    root, task, _scope, environment = broker_repo
    _state_root, status_path, requests_dir, _claimed_dir, responses_dir = broker.broker_paths(root)
    broker._write_status(status_path, "IDLE")
    request = broker.make_request(root, task, run_prefix, 30, environment)
    response_path = broker._response_path(responses_dir, request["request_id"])
    return root, request, response_path, requests_dir


def _make_valid_response(
    request: dict,
    stdout: str = "success\n",
    stderr: str = "warning\n",
    exit_code: int = 0,
) -> dict:
    return {
        "schema_version": "1.0",
        "request_id": request["request_id"],
        "run_prefix": request["run_prefix"],
        "source_head": request["source_head"],
        "state": "COMPLETED",
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "finished_at": time.time(),
    }


def test_client_custody_success_reads_outputs_and_unlinks(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]], capsys: pytest.CaptureFixture
) -> None:
    root, request, response_path, requests_dir = _prepare_client_context(broker_repo, "custody-succ")
    valid_resp = _make_valid_response(request, stdout='{"state":"READY"}\n', stderr="log msg\n", exit_code=0)
    broker._atomic_new_json(response_path, valid_resp, broker.MAX_RESPONSE_BYTES)

    result = broker._submit_request(root, request, 30, maintenance=False)
    assert result == 0
    assert not response_path.exists()

    captured = capsys.readouterr()
    assert captured.out == '{"state":"READY"}\n'
    assert captured.err == "log msg\n"

    request_file = requests_dir / f"{request['request_id']}.json"
    assert request_file.exists()


def test_client_custody_malformed_json_leaves_response(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]]
) -> None:
    root, request, response_path, requests_dir = _prepare_client_context(broker_repo, "custody-malform")
    response_path.write_bytes(b"not json{")
    os.chmod(response_path, 0o600)

    with pytest.raises(broker.BrokerError, match="not one valid JSON document"):
        broker._submit_request(root, request, 30, maintenance=False)

    assert response_path.exists()
    assert response_path.read_bytes() == b"not json{"
    request_file = requests_dir / f"{request['request_id']}.json"
    assert request_file.exists()


def test_client_custody_wrong_binding_leaves_response(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]]
) -> None:
    root, request, response_path, requests_dir = _prepare_client_context(broker_repo, "custody-bind")
    mismatched_resp = _make_valid_response(request)
    mismatched_resp["request_id"] = "0" * 32
    broker._atomic_new_json(response_path, mismatched_resp, broker.MAX_RESPONSE_BYTES)

    with pytest.raises(broker.BrokerError, match="broker response binding is invalid"):
        broker._submit_request(root, request, 30, maintenance=False)

    assert response_path.exists()
    request_file = requests_dir / f"{request['request_id']}.json"
    assert request_file.exists()


def test_client_custody_unsafe_file_leaves_response(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]]
) -> None:
    root, request, response_path, requests_dir = _prepare_client_context(broker_repo, "custody-unsafe")
    valid_resp = _make_valid_response(request)
    oversized = dict(valid_resp)
    oversized["stdout"] = "x" * (broker.MAX_RESPONSE_BYTES + 1)
    encoded = json.dumps(oversized).encode("utf-8")
    response_path.write_bytes(encoded)
    os.chmod(response_path, 0o600)

    with pytest.raises(broker.BrokerError, match="exceeds byte limit|is not a trusted queue file"):
        broker._submit_request(root, request, 30, maintenance=False)

    assert response_path.exists()
    request_file = requests_dir / f"{request['request_id']}.json"
    assert request_file.exists()


def test_client_custody_stdout_write_fault_leaves_response(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]]
) -> None:
    root, request, response_path, requests_dir = _prepare_client_context(broker_repo, "custody-out-wr")
    valid_resp = _make_valid_response(request, stdout="some stdout\n", stderr="some stderr\n")
    broker._atomic_new_json(response_path, valid_resp, broker.MAX_RESPONSE_BYTES)

    with mock.patch.object(sys.stdout, "write", side_effect=OSError("stdout write failed")):
        with pytest.raises(OSError, match="stdout write failed"):
            broker._submit_request(root, request, 30, maintenance=False)

    assert response_path.exists()
    request_file = requests_dir / f"{request['request_id']}.json"
    assert request_file.exists()


def test_client_custody_stdout_flush_fault_leaves_response(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]]
) -> None:
    root, request, response_path, requests_dir = _prepare_client_context(broker_repo, "custody-out-flt")
    valid_resp = _make_valid_response(request, stdout="some stdout\n", stderr="some stderr\n")
    broker._atomic_new_json(response_path, valid_resp, broker.MAX_RESPONSE_BYTES)

    with mock.patch.object(sys.stdout, "flush", side_effect=OSError("stdout flush failed")):
        with pytest.raises(OSError, match="stdout flush failed"):
            broker._submit_request(root, request, 30, maintenance=False)

    assert response_path.exists()
    request_file = requests_dir / f"{request['request_id']}.json"
    assert request_file.exists()


def test_client_custody_stderr_write_fault_leaves_response(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]]
) -> None:
    root, request, response_path, requests_dir = _prepare_client_context(broker_repo, "custody-err-wr")
    valid_resp = _make_valid_response(request, stdout="some stdout\n", stderr="some stderr\n")
    broker._atomic_new_json(response_path, valid_resp, broker.MAX_RESPONSE_BYTES)

    with mock.patch.object(sys.stderr, "write", side_effect=OSError("stderr write failed")):
        with pytest.raises(OSError, match="stderr write failed"):
            broker._submit_request(root, request, 30, maintenance=False)

    assert response_path.exists()
    request_file = requests_dir / f"{request['request_id']}.json"
    assert request_file.exists()


def test_client_custody_stderr_flush_fault_leaves_response(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]]
) -> None:
    root, request, response_path, requests_dir = _prepare_client_context(broker_repo, "custody-err-flt")
    valid_resp = _make_valid_response(request, stdout="some stdout\n", stderr="some stderr\n")
    broker._atomic_new_json(response_path, valid_resp, broker.MAX_RESPONSE_BYTES)

    with mock.patch.object(sys.stderr, "flush", side_effect=OSError("stderr flush failed")):
        with pytest.raises(OSError, match="stderr flush failed"):
            broker._submit_request(root, request, 30, maintenance=False)

    assert response_path.exists()
    request_file = requests_dir / f"{request['request_id']}.json"
    assert request_file.exists()


def test_client_custody_unlink_fault_surfaces(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]]
) -> None:
    root, request, response_path, requests_dir = _prepare_client_context(broker_repo, "custody-unlink")
    valid_resp = _make_valid_response(request)
    broker._atomic_new_json(response_path, valid_resp, broker.MAX_RESPONSE_BYTES)

    original_unlink = Path.unlink

    def faulty_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == response_path.name:
            raise OSError("simulated unlink fault")
        original_unlink(self, *args, **kwargs)

    with mock.patch.object(Path, "unlink", side_effect=faulty_unlink, autospec=True):
        with pytest.raises(OSError, match="simulated unlink fault"):
            broker._submit_request(root, request, 30, maintenance=False)

    assert response_path.exists()
    request_file = requests_dir / f"{request['request_id']}.json"
    assert request_file.exists()
