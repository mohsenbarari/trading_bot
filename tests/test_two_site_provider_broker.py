from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import threading
import time
from unittest import mock

import pytest

from scripts import two_site_provider_broker as broker
from scripts.two_site_assignment_scope import AssignmentScope
from scripts.two_site_preparation_contract import PreparationStartup


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


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
        "task_id": "BROKER-TEST-1",
        "goal": "WA-BROKER-TEST: create one bounded characterization test.",
        "context_files": ["source.py"],
        "allowed_files": ["tests/test_new.py"],
        "requirements": ["Cover the bounded outcome."],
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


def make_valid_request(
    root: Path, task: Path, environment: dict[str, str]
) -> dict:
    return broker.make_request(root, task, "broker-test-run", 30, environment)


def test_request_is_bound_to_task_head_assignment_and_controller(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]],
) -> None:
    root, task, scope, environment = broker_repo
    request = make_valid_request(root, task, environment)

    validated, validated_path, validated_scope = broker.validate_request(
        root, request, scope_loader=lambda _root: scope
    )

    assert validated == request
    assert validated_path == task
    assert validated_scope == scope
    assert request["source_head"] == git(root, "rev-parse", "HEAD")
    assert request["task_sha256"] == hashlib.sha256(task.read_bytes()).hexdigest()
    assert request["controller_run_id"] == environment["TWO_SITE_AUTOPILOT_RUN_ID"]


@pytest.mark.parametrize(
    "mutation,error",
    [
        (lambda value: value.update(extra="x"), "exact contract"),
        (lambda value: value.update(source_head="0" * 40), "HEAD drift"),
        (lambda value: value.update(task_sha256="0" * 64), "task SHA drift"),
        (lambda value: value.update(created_at=time.time() - 301), "stale"),
        (lambda value: value.update(run_prefix="bad/prefix"), "run-prefix"),
        (lambda value: value.update(controller_run_id="not-a-cycle"), "controller run ID"),
    ],
)
def test_request_contract_rejects_drift(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]], mutation, error: str
) -> None:
    root, task, scope, environment = broker_repo
    request = make_valid_request(root, task, environment)
    mutation(request)
    with pytest.raises(broker.BrokerError, match=error):
        broker.validate_request(root, request, scope_loader=lambda _root: scope)


def test_request_rejects_outside_and_hardlinked_task(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]], tmp_path: Path
) -> None:
    root, task, _scope, environment = broker_repo
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(broker.BrokerError, match="outside the project root"):
        broker.make_request(root, outside, "outside-task", 30, environment)

    hardlink = task.with_name("hardlink.json")
    os.link(task, hardlink)
    with pytest.raises(broker.ProposalError, match="hardlinked"):
        broker.make_request(root, task, "hardlink-task", 30, environment)


def test_execute_pipeline_uses_only_fixed_command_and_bound_environment(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]],
) -> None:
    root, task, scope, environment = broker_repo
    request = make_valid_request(root, task, environment)
    process = mock.MagicMock()
    process.returncode = 0
    process.__enter__.return_value = process
    process.__exit__.return_value = False

    def fake_capture(_process, _stdin, stdout_path, stderr_path, _timeout, max_output):
        assert max_output == broker.MAX_PIPELINE_OUTPUT_BYTES
        stdout_path.write_text('{"state":"READY_FOR_CODEX"}\n', encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")

    with mock.patch.object(broker.subprocess, "Popen", return_value=process) as popen, \
            mock.patch.object(broker, "capture_bounded_process", side_effect=fake_capture):
        response = broker.execute_pipeline(root, request, task, scope)

    command = popen.call_args.args[0]
    assert command == [
        "/usr/bin/python3", str(root / broker.PIPELINE_REL), "--root", str(root),
        "--task", str(task), "--run-prefix", request["run_prefix"],
        "--timeout", str(request["timeout"]),
    ]
    child_env = popen.call_args.kwargs["env"]
    assert child_env["TWO_SITE_ASSIGNMENT_ID"] == scope.assignment_id
    assert child_env["TWO_SITE_ASSIGNMENT_SHA256"] == scope.sha256
    assert child_env["TWO_SITE_AUTOPILOT_RUN_ID"] == request["controller_run_id"]
    assert response["state"] == "COMPLETED"
    assert response["exit_code"] == 0
    assert "READY_FOR_CODEX" in response["stdout"]


def test_filesystem_queue_round_trip_preserves_exact_pipeline_result(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]], capsys
) -> None:
    root, task, scope, environment = broker_repo

    def fake_executor(_root, request, _task_path, _scope):
        return {
            "schema_version": "1.0",
            "request_id": request["request_id"],
            "run_prefix": request["run_prefix"],
            "source_head": request["source_head"],
            "state": "COMPLETED",
            "exit_code": 0,
            "stdout": '{"state":"READY_FOR_CODEX"}\n',
            "stderr": "bounded warning\n",
            "finished_at": time.time(),
        }

    thread = threading.Thread(
        target=broker.serve,
        kwargs={
            "root": root,
            "once": True,
            "executor": fake_executor,
            "scope_loader": lambda _root: scope,
            "install_signal_handlers": False,
        },
        daemon=True,
    )
    thread.start()
    _state_root, status_path, _requests, _claimed, _responses = broker.broker_paths(root)
    deadline = time.time() + 5
    while not status_path.exists() and time.time() < deadline:
        time.sleep(0.01)
    assert status_path.exists()

    cycle_id = environment["TWO_SITE_AUTOPILOT_RUN_ID"]
    run_dir = root / ".local/two-site-refactor/autopilot/runs" / cycle_id
    run_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(run_dir, 0o700)
    startup_payload = PreparationStartup(
        schema_version=1,
        route="one_shot",
        cycle_id=environment["TWO_SITE_AUTOPILOT_RUN_ID"],
        source_head=git(root, "rev-parse", "HEAD"),
        assignment_sha256=scope.sha256,
    ).to_mapping()
    startup_file = run_dir / "preparation-startup.json"
    startup_file.write_text(
        json.dumps(startup_payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.chmod(startup_file, 0o600)

    result = broker.submit(root, task, "queue-round-trip", 30, environment)
    thread.join(timeout=5)
    assert result == 0
    assert not thread.is_alive()
    captured = capsys.readouterr()
    assert captured.out == '{"state":"READY_FOR_CODEX"}\n'
    assert captured.err == "bounded warning\n"


def test_restart_fails_ambiguous_claim_without_reexecuting(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]], capsys
) -> None:
    root, task, scope, environment = broker_repo
    request = make_valid_request(root, task, environment)
    _state_root, _status, _requests, claimed, responses = broker.broker_paths(root)
    broker._atomic_new_json(
        claimed / f"{request['request_id']}.json", request, broker.MAX_REQUEST_BYTES
    )
    executor = mock.Mock()

    thread = threading.Thread(
        target=broker.serve,
        kwargs={
            "root": root,
            "once": True,
            "executor": executor,
            "scope_loader": lambda _root: scope,
            "install_signal_handlers": False,
        },
        daemon=True,
    )
    thread.start()
    response_path = responses / f"{request['request_id']}.json"
    deadline = time.time() + 5
    while not response_path.exists() and time.time() < deadline:
        time.sleep(0.01)
    assert response_path.exists()
    response = broker._read_json_file(root, response_path, broker.MAX_RESPONSE_BYTES, "response")
    assert response["state"] == "BROKER_ERROR"
    assert "ambiguous in-flight" in response["stderr"]
    executor.assert_not_called()
    broker._STOP = True
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_response_rejects_binding_and_output_drift(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]],
) -> None:
    root, task, _scope, environment = broker_repo
    request = make_valid_request(root, task, environment)
    response = {
        "schema_version": "1.0",
        "request_id": request["request_id"],
        "run_prefix": request["run_prefix"],
        "source_head": request["source_head"],
        "state": "COMPLETED",
        "exit_code": 0,
        "stdout": "ok",
        "stderr": "",
        "finished_at": time.time(),
    }
    assert broker.validate_response(request, response) == response
    response["request_id"] = "f" * 32
    with pytest.raises(broker.BrokerError, match="binding"):
        broker.validate_response(request, response)


def test_atomic_queue_publication_never_overwrites_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "request.json"
    target.write_text("owner-data", encoding="utf-8")
    with pytest.raises(broker.BrokerError, match="target already exists"):
        broker._atomic_new_json(target, {"new": "value"}, 1000)
    assert target.read_text(encoding="utf-8") == "owner-data"


@pytest.mark.parametrize(
    "scenario,target_pre_exists,patch_attr,patch_spec,expected_exc,expected_match",
    [
        ("success", False, None, None, None, None),
        (
            "write_zero",
            False,
            "write",
            {"return_value": 0},
            broker.BrokerError,
            "write made no progress",
        ),
        (
            "fsync_error",
            False,
            "fsync",
            {"side_effect": OSError("fsync failure")},
            OSError,
            "fsync failure",
        ),
        (
            "fchmod_error",
            False,
            "fchmod",
            {"side_effect": OSError("fchmod failure")},
            OSError,
            "fchmod failure",
        ),
        (
            "link_collision",
            False,
            "link",
            {"side_effect": FileExistsError("collision")},
            broker.BrokerError,
            "appeared during publication",
        ),
        (
            "link_oserror",
            False,
            "link",
            {"side_effect": OSError("link failure")},
            OSError,
            "link failure",
        ),
        (
            "pre_existing_target",
            True,
            None,
            None,
            broker.BrokerError,
            "target already exists",
        ),
    ],
)
def test_atomic_new_json_cleanup_and_publication(
    tmp_path: Path,
    scenario: str,
    target_pre_exists: bool,
    patch_attr: str | None,
    patch_spec: dict | None,
    expected_exc: type[BaseException] | None,
    expected_match: str | None,
) -> None:
    target = tmp_path / "published.json"
    payload = {"key": "value", "id": 123}
    expected_bytes = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    initial_content = b"prior-data-must-not-change"
    if target_pre_exists:
        target.write_bytes(initial_content)

    cm = (
        mock.patch.object(broker.os, patch_attr, **patch_spec)
        if patch_attr
        else contextlib.nullcontext()
    )

    if expected_exc is not None:
        with cm:
            with pytest.raises(expected_exc, match=expected_match):
                broker._atomic_new_json(target, payload, 1000)
        if target_pre_exists:
            assert target.read_bytes() == initial_content
            assert [p.name for p in tmp_path.iterdir() if p != target] == []
        else:
            assert not target.exists()
            assert list(tmp_path.iterdir()) == []
    else:
        with cm:
            broker._atomic_new_json(target, payload, 1000)
        assert target.exists()
        assert target.read_bytes() == expected_bytes
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert [p.name for p in tmp_path.iterdir() if p != target] == []

    temps = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert temps == []


def test_queue_consumer_waits_for_single_link_publication(tmp_path: Path) -> None:
    temporary = tmp_path / ".request.tmp"
    published = tmp_path / "request.json"
    temporary.write_text("{}", encoding="utf-8")
    temporary.chmod(0o600)
    os.link(temporary, published)
    assert broker._published_queue_file(published, "request") is False
    temporary.unlink()
    assert broker._published_queue_file(published, "request") is True


def test_orphan_response_cleanup_is_bounded_and_retained(tmp_path: Path) -> None:
    old = tmp_path / "old.json"
    recent = tmp_path / "recent.json"
    old.write_text("{}", encoding="utf-8")
    recent.write_text("{}", encoding="utf-8")
    now = time.time()
    old_time = now - broker.ORPHAN_RESPONSE_RETENTION_SECONDS - 1
    os.utime(old, (old_time, old_time))

    assert broker._prune_orphan_responses(tmp_path, now=now) == 1
    assert not old.exists()
    assert recent.exists()


def test_status_uses_fresh_heartbeat_not_cross_namespace_pid_visibility(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]], capsys
) -> None:
    root, _task, _scope, _environment = broker_repo
    _state_root, status_path, _requests, _claimed, _responses = broker.broker_paths(root)
    broker.write_json(status_path, {
        "schema_version": "1.0",
        "state": "IDLE",
        "pid": 999_999_999,
        "updated_at": time.time(),
    })
    status_path.chmod(0o600)

    assert broker.status(root) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "IDLE"


def test_broker_status_retries_only_an_atomic_snapshot_transition(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]], monkeypatch
) -> None:
    root, _task, _scope, _environment = broker_repo
    _state_root, status_path, _requests, _claimed, _responses = broker.broker_paths(root)
    transition = broker.BrokerError("broker status is not one valid JSON document")
    transition.__cause__ = broker.ProposalError(
        "broker status changed while it was being read"
    )
    valid = {"schema_version": "1.0", "state": "IDLE", "pid": 12, "updated_at": time.time()}
    sleep = mock.Mock()
    with mock.patch.object(broker, "_read_json_file", side_effect=[transition, valid]) as read:
        monkeypatch.setattr(broker.time, "sleep", sleep)
        assert broker._read_broker_status(root, status_path) == valid
    assert read.call_count == 2
    sleep.assert_called_once_with(broker.STATUS_READ_RETRY_SECONDS)


def test_broker_status_never_retries_or_accepts_malformed_json(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]], monkeypatch
) -> None:
    root, _task, _scope, _environment = broker_repo
    _state_root, status_path, _requests, _claimed, _responses = broker.broker_paths(root)
    malformed = broker.BrokerError("broker status is not one valid JSON document")
    malformed.__cause__ = json.JSONDecodeError("bad", "{", 1)
    sleep = mock.Mock()
    with mock.patch.object(broker, "_read_json_file", side_effect=malformed) as read:
        monkeypatch.setattr(broker.time, "sleep", sleep)
        with pytest.raises(broker.BrokerError, match="not one valid JSON"):
            broker._read_broker_status(root, status_path)
    assert read.call_count == 1
    sleep.assert_not_called()


def test_status_writer_uses_a_unique_private_atomic_temporary_path(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]], monkeypatch
) -> None:
    root, _task, _scope, _environment = broker_repo
    _state_root, status_path, _requests, _claimed, _responses = broker.broker_paths(root)
    names: list[Path] = []
    original_open = broker.os.open

    def record_open(path, flags, mode=0o777):
        names.append(Path(path))
        return original_open(path, flags, mode)

    monkeypatch.setattr(broker.os, "open", record_open)
    broker._write_status(status_path, "IDLE")
    assert names
    temporary = names[0]
    assert temporary.parent == status_path.parent
    assert temporary.name.startswith(f".{status_path.name}.")
    assert temporary.name.endswith(".tmp")
    assert temporary != status_path.with_suffix(status_path.suffix + ".tmp")
    assert not temporary.exists()
    assert broker._read_json_file(root, status_path, broker.MAX_REQUEST_BYTES, "status")["state"] == "IDLE"


def test_status_writer_removes_its_private_temp_after_a_failed_write(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]], monkeypatch
) -> None:
    _root, _task, _scope, _environment = broker_repo
    state_root, status_path, _requests, _claimed, _responses = broker.broker_paths(_root)
    monkeypatch.setattr(broker.os, "write", lambda _descriptor, _data: 0)
    with pytest.raises(broker.BrokerError, match="write made no progress"):
        broker._write_status(status_path, "IDLE")
    assert not status_path.exists()
    assert list(state_root.glob(f".{status_path.name}.*.tmp")) == []


def test_running_provider_call_refreshes_filesystem_heartbeat(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]], monkeypatch
) -> None:
    root, task, scope, environment = broker_repo
    request = make_valid_request(root, task, environment)
    status_path = root / ".local/status.json"
    writes: list[str] = []

    def fake_write(_path: Path, state: str, **_fields) -> None:
        writes.append(state)

    def slow_executor(_root, _request, _task, _scope):
        time.sleep(0.05)
        return {"ok": True}

    monkeypatch.setattr(broker, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(broker, "_write_status", fake_write)
    result = broker._execute_with_heartbeat(
        status_path, slow_executor, root, request, task, scope
    )
    assert result == {"ok": True}
    assert writes.count("RUNNING") >= 2


def test_normal_stop_waits_for_supervisor_boundary_acknowledgement(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]], monkeypatch
) -> None:
    root, _task, _scope, _environment = broker_repo
    state_root = root / ".local/two-site-refactor/autopilot"
    state_root.mkdir(parents=True)
    (state_root / "STOP").write_text("controlled\n", encoding="utf-8")
    broker.write_json(state_root / "status.json", {"state": "RUNNING"})
    sleeps = 0

    def acknowledge_after_one_poll(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        broker.write_json(state_root / "status.json", {"state": "STOPPED"})

    monkeypatch.setattr(broker.time, "sleep", acknowledge_after_one_poll)
    result = broker.serve(
        root,
        once=False,
        scope_loader=lambda _root: (_ for _ in ()).throw(AssertionError("no request")),
        install_signal_handlers=False,
    )

    assert result == 0
    assert sleeps == 1


def test_exclusive_lock_rejects_second_broker(tmp_path: Path) -> None:
    lock = tmp_path / "broker.lock"
    with broker.exclusive_broker_lock(lock):
        with pytest.raises(broker.BrokerError, match="already owns"):
            with broker.exclusive_broker_lock(lock):
                pass


def _stopped_supervisor(root: Path) -> Path:
    state_root = root / ".local/two-site-refactor/autopilot"
    state_root.mkdir(parents=True, exist_ok=True)
    broker.write_json(state_root / "status.json", {"state": "STOPPED", "cycle": 1})
    return state_root


def test_maintenance_round_trip_generates_bound_identity_and_holds_supervisor_lock(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]], monkeypatch, capsys
) -> None:
    root, task, scope, _environment = broker_repo
    state_root = _stopped_supervisor(root)
    monkeypatch.setattr(broker, "load_active_assignment_scope", lambda _root: scope)
    # A stale caller cycle tag must never leak into a maintenance submission.
    monkeypatch.setenv("TWO_SITE_AUTOPILOT_RUN_ID", "cycle-0001-20990101T000000Z-123")
    observed: list[str] = []

    def fake_executor(_root, request, _task_path, _scope):
        run_id = request["controller_run_id"]
        assert broker.MAINTENANCE_RUN_RE.fullmatch(run_id)
        assert broker._read_json_file(
            root, broker._maintenance_record_path(root, run_id),
            broker.MAX_MAINTENANCE_RECORD_BYTES, "maintenance record",
        ) == {"schema_version": "1.0", "request": request}
        with pytest.raises(broker.BrokerError, match="already owns"):
            with broker.exclusive_broker_lock(state_root / "autopilot.lock"):
                pass
        observed.append(run_id)
        return {
            "schema_version": "1.0", "request_id": request["request_id"],
            "run_prefix": request["run_prefix"], "source_head": request["source_head"],
            "state": "COMPLETED", "exit_code": 0, "stdout": "maintenance-ok\n",
            "stderr": "", "finished_at": time.time(),
        }

    thread = threading.Thread(
        target=broker.serve,
        kwargs={
            "root": root, "once": True, "executor": fake_executor,
            "scope_loader": lambda _root: scope, "install_signal_handlers": False,
        },
        daemon=True,
    )
    thread.start()
    _state, broker_status, _requests, _claimed, _responses = broker.broker_paths(root)
    deadline = time.time() + 5
    while not broker_status.exists() and time.time() < deadline:
        time.sleep(0.01)
    assert broker.submit_maintenance(root, task, "maintenance-test", 30) == 0
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(observed) == 1
    assert capsys.readouterr().out == "maintenance-ok\n"


def test_maintenance_record_is_required_and_bound_to_exact_request(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]],
) -> None:
    root, task, scope, environment = broker_repo
    state_root = _stopped_supervisor(root)
    maintenance_environment = dict(environment)
    maintenance_environment.pop("TWO_SITE_AUTOPILOT_RUN_ID")
    maintenance_environment["TWO_SITE_MAINTENANCE_RUN_ID"] = (
        "maint-20990101T000000Z-1234567890abcdef"
    )
    request = broker.make_request(
        root, task, "maintenance-test", 30, maintenance_environment,
        maintenance=True,
    )
    with pytest.raises(broker.ProposalError, match="maintenance record"):
        broker.validate_request(root, request, scope_loader=lambda _root: scope)
    broker._publish_maintenance_record(root, request)
    assert broker.validate_request(root, request, scope_loader=lambda _root: scope)[0] == request
    modified = dict(request, run_prefix="different")
    with pytest.raises(broker.BrokerError, match="record binding mismatch"):
        broker.validate_request(root, modified, scope_loader=lambda _root: scope)
    (state_root / "STOP").touch()
    with pytest.raises(broker.BrokerError, match="cleared STOP"):
        broker.validate_request(root, request, scope_loader=lambda _root: scope)
    (state_root / "STOP").unlink()
    broker.write_json(state_root / "status.json", {"state": "RUNNING", "cycle": 1})
    with pytest.raises(broker.BrokerError, match="stopped Supervisor"):
        broker.validate_request(root, request, scope_loader=lambda _root: scope)


def test_normal_submit_cannot_claim_maintenance_or_ambiguous_provenance(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]],
) -> None:
    root, task, _scope, environment = broker_repo
    maintenance = dict(environment)
    maintenance.pop("TWO_SITE_AUTOPILOT_RUN_ID")
    maintenance["TWO_SITE_MAINTENANCE_RUN_ID"] = "maint-20990101T000000Z-1234567890abcdef"
    with pytest.raises(broker.BrokerError, match="rejects maintenance"):
        broker.make_request(root, task, "ordinary", 30, maintenance)
    both = dict(environment, TWO_SITE_MAINTENANCE_RUN_ID=maintenance["TWO_SITE_MAINTENANCE_RUN_ID"])
    with pytest.raises(broker.BrokerError, match="ambiguous"):
        broker.make_request(root, task, "ordinary", 30, both)
    with pytest.raises(broker.BrokerError, match="ambiguous"):
        broker.make_request(root, task, "maintenance", 30, both, maintenance=True)


def test_maintenance_pipeline_uses_only_distinct_provenance_environment(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]], monkeypatch
) -> None:
    root, task, scope, environment = broker_repo
    maintenance = dict(environment)
    maintenance.pop("TWO_SITE_AUTOPILOT_RUN_ID")
    maintenance["TWO_SITE_MAINTENANCE_RUN_ID"] = "maint-20990101T000000Z-1234567890abcdef"
    request = broker.make_request(
        root, task, "maintenance-test", 30, maintenance, maintenance=True
    )
    monkeypatch.setenv("TWO_SITE_AUTOPILOT_RUN_ID", environment["TWO_SITE_AUTOPILOT_RUN_ID"])
    process = mock.MagicMock()
    process.returncode = 0
    process.__enter__.return_value = process
    process.__exit__.return_value = False

    def fake_capture(_process, _stdin, stdout_path, stderr_path, _timeout, max_output):
        stdout_path.write_text("ok\n", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")

    with mock.patch.object(broker.subprocess, "Popen", return_value=process) as popen, \
            mock.patch.object(broker, "capture_bounded_process", side_effect=fake_capture):
        assert broker.execute_pipeline(root, request, task, scope)["exit_code"] == 0
    child_environment = popen.call_args.kwargs["env"]
    assert child_environment["TWO_SITE_MAINTENANCE_RUN_ID"] == request["controller_run_id"]
    assert "TWO_SITE_AUTOPILOT_RUN_ID" not in child_environment


def test_maintenance_record_pruning_preserves_recent_and_unowned_files(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]]
) -> None:
    root, _task, _scope, _environment = broker_repo
    directory = broker._maintenance_record_directory(root)
    old = directory / "maint-20200101T000000Z-1234567890abcdef.json"
    recent = directory / "maint-20990101T000000Z-1234567890abcdef.json"
    unrelated = directory / "unrelated.json"
    for path in (old, recent, unrelated):
        path.write_text("{}", encoding="utf-8")
    now = time.time()
    aged = now - broker.MAINTENANCE_RECORD_RETENTION_SECONDS - 1
    os.utime(old, (aged, aged))
    os.utime(unrelated, (aged, aged))
    assert broker._prune_maintenance_records(root, now=now) == 1
    assert not old.exists()
    assert recent.exists()
    assert unrelated.exists()


def _write_runner_status(
    root: Path,
    run_id: str,
    *,
    role: str,
    head: str,
    model: str,
    task_id: str,
    quota_evidence: dict,
    conversation_id: str | None = "conv-test",
    state: str = "BLOCKED",
    raw_content: bytes | None = None,
) -> tuple[Path, str]:
    run_dir = root / ".local/two-site-refactor/proposals" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "status.json"
    if raw_content is not None:
        status_path.write_bytes(raw_content)
        return status_path, hashlib.sha256(raw_content).hexdigest()
    status_data = {
        "run_id": run_id,
        "role": role,
        "head": head,
        "model": model,
        "state": state,
        "task_id": task_id,
        "quota_evidence": quota_evidence,
    }
    if conversation_id is not None:
        status_data["conversation_id"] = conversation_id
    content = json.dumps(status_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    status_path.write_bytes(content)
    status_sha = hashlib.sha256(content).hexdigest()
    return status_path, status_sha


def _make_quota_payload(
    root: Path,
    task_id: str,
    source_head: str,
    run_prefix: str = "broker-test-run",
    *,
    role: str = "worker",
    kind: str = "QUOTA_RESET_KNOWN",
    is_correction: bool = False,
    run_id: str | None = None,
    corrupt_status_sha: bool = False,
    extra_attempt_keys: dict | None = None,
    omit_attempt_key: str | None = None,
    status_raw_content: bytes | None = None,
    conversation_id: str | None = None,
    status_role: str | None = None,
    status_head: str | None = None,
    status_model: str | None = None,
    status_task_id: str | None = None,
    status_state: str = "BLOCKED",
    create_result_file: bool = False,
    create_patch_file: bool = False,
) -> dict:
    if role == "worker":
        attempt_role = "worker"
        model = broker.WORKER_MODEL
        if is_correction:
            effective_run_id = run_id or f"{run_prefix}-worker-1"
            worker_run_id = f"{run_prefix}-worker-0"
            reviewer_run_id = f"{run_prefix}-reviewer-0"
            proposal_sha = "1" * 64
            corr_count = 1
        else:
            effective_run_id = run_id or f"{run_prefix}-worker-0"
            worker_run_id = None
            reviewer_run_id = None
            proposal_sha = None
            corr_count = 0
    else:
        attempt_role = "verifier"
        effective_run_id = run_id or f"{run_prefix}-reviewer-0"
        model = broker.MODEL
        worker_run_id = f"{run_prefix}-worker-0"
        reviewer_run_id = None
        proposal_sha = "1" * 64
        corr_count = 0

    cid = conversation_id if conversation_id is not None else f"conv-{role}-test"
    evidence = {
        "kind": kind,
        "provider": "gemini",
        "model": model,
        "conversation_id": cid,
        "reset_at_utc": "2099-01-01T00:00:00Z" if kind == "QUOTA_RESET_KNOWN" else None,
    }

    _status_path, status_sha = _write_runner_status(
        root,
        effective_run_id,
        role=status_role or attempt_role,
        head=status_head or source_head,
        model=status_model or model,
        task_id=status_task_id or task_id,
        quota_evidence=evidence,
        conversation_id=cid,
        state=status_state,
        raw_content=status_raw_content,
    )

    if create_result_file:
        (root / ".local/two-site-refactor/proposals" / effective_run_id / "result.json").write_text("{}", encoding="utf-8")
    if create_patch_file:
        (root / ".local/two-site-refactor/proposals" / effective_run_id / "proposal.patch").write_text("", encoding="utf-8")

    attempt = {
        "role": attempt_role,
        "run_id": effective_run_id,
        "status_sha256": "0" * 64 if corrupt_status_sha else status_sha,
    }
    if extra_attempt_keys:
        attempt.update(extra_attempt_keys)
    if omit_attempt_key:
        del attempt[omit_attempt_key]

    return {
        "state": "TASK_BLOCKED",
        "task_id": task_id,
        "source_head": source_head,
        "final_worker_run_id": worker_run_id,
        "final_reviewer_run_id": reviewer_run_id,
        "proposal_sha256": proposal_sha,
        "changed_paths": [],
        "proposal_path": None,
        "reviewer_path": None,
        "correction_count": corr_count,
        "blockers": ["observational quota, no replay authority"],
        "pre_review_test_path": None,
        "pre_review_test_sha256": None,
        "pre_review_test_state": None,
        "semantic_task_fingerprint": None,
        "quota_evidence": evidence,
        "quota_attempt": attempt,
    }


def _make_quota_response(request: dict, payload: dict) -> dict:
    return {
        "schema_version": "1.0",
        "request_id": request["request_id"],
        "run_prefix": request["run_prefix"],
        "source_head": request["source_head"],
        "state": "COMPLETED",
        "exit_code": 1,
        "stdout": json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        "stderr": "",
        "finished_at": time.time(),
    }


@pytest.mark.parametrize("kind", ["QUOTA_RESET_KNOWN", "QUOTA_RESET_UNKNOWN"])
def test_detect_quota_response_positive_initial_worker_reviewer_and_correction_worker(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]],
    kind: str,
) -> None:
    root, task, _scope, environment = broker_repo
    request = make_valid_request(root, task, environment)

    # 1. Initial Worker
    payload_worker = _make_quota_payload(
        root, "BROKER-TEST-1", request["source_head"], role="worker", kind=kind, is_correction=False
    )
    resp_worker = _make_quota_response(request, payload_worker)
    det_worker = broker.detect_quota_response(root, request, resp_worker)
    assert det_worker is not None
    assert det_worker == payload_worker["quota_evidence"]
    assert broker.detect_terminal_quota_response(root, request, resp_worker) == det_worker

    # 2. Reviewer (verifier)
    payload_rev = _make_quota_payload(
        root, "BROKER-TEST-1", request["source_head"], role="verifier", kind=kind, is_correction=False
    )
    resp_rev = _make_quota_response(request, payload_rev)
    det_rev = broker.detect_quota_response(root, request, resp_rev)
    assert det_rev is not None
    assert det_rev == payload_rev["quota_evidence"]
    assert broker.detect_terminal_quota_response(root, request, resp_rev) == det_rev

    # 3. CORRECTION Worker with prior stale IDs
    payload_corr = _make_quota_payload(
        root, "BROKER-TEST-1", request["source_head"], role="worker", kind=kind, is_correction=True
    )
    assert payload_corr["final_worker_run_id"] == f"{request['run_prefix']}-worker-0"
    assert payload_corr["final_reviewer_run_id"] == f"{request['run_prefix']}-reviewer-0"
    assert payload_corr["proposal_sha256"] == "1" * 64
    assert payload_corr["quota_attempt"]["role"] == "worker"
    assert payload_corr["quota_evidence"]["model"] == broker.WORKER_MODEL

    resp_corr = _make_quota_response(request, payload_corr)
    det_corr = broker.detect_quota_response(root, request, resp_corr)
    assert det_corr is not None
    assert det_corr == payload_corr["quota_evidence"]
    assert broker.detect_terminal_quota_response(root, request, resp_corr) == det_corr


def test_detect_quota_response_negative_matrix(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]],
) -> None:
    root, task, _scope, environment = broker_repo
    request = make_valid_request(root, task, environment)
    payload = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], role="worker")
    response = _make_quota_response(request, payload)

    assert broker.detect_quota_response(root, request, response) is not None

    # Untrusted request / response types
    assert broker.detect_quota_response(root, None, response) is None
    assert broker.detect_quota_response(root, request, None) is None
    assert broker.detect_quota_response(root, "bad", response) is None

    # Wrong request ID
    assert broker.detect_quota_response(root, request, dict(response, request_id="0" * 32)) is None

    # Wrong head
    assert broker.detect_quota_response(root, request, dict(response, source_head="0" * 40)) is None
    p_head = dict(payload, source_head="0" * 40)
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_head)) is None
    p_st_head = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], status_head="0" * 40)
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_st_head)) is None

    # Wrong task SHA
    bad_sha_req = dict(request, task_sha256="0" * 64)
    assert broker.detect_quota_response(root, bad_sha_req, response) is None

    # Wrong task ID
    p_tid = dict(payload, task_id="WRONG-ID")
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_tid)) is None
    p_st_tid = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], status_task_id="WRONG-ID")
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_st_tid)) is None

    # Malformed, duplicate, and trailing JSON
    assert broker.detect_quota_response(root, request, dict(response, stdout="bad json {")) is None
    dup_json = '{"state":"TASK_BLOCKED","state":"TASK_BLOCKED"}'
    assert broker.detect_quota_response(root, request, dict(response, stdout=dup_json)) is None
    assert broker.detect_quota_response(root, request, dict(response, stdout=response["stdout"] + " trailing")) is None

    # Wrong blocker
    for bad_blockers in [[], ["other"], ["observational quota, no replay authority", "extra"]]:
        p_blk = dict(payload, blockers=bad_blockers)
        assert broker.detect_quota_response(root, request, _make_quota_response(request, p_blk)) is None

    # Wrong pipeline state, exit code, or stderr
    assert broker.detect_quota_response(root, request, dict(response, state="BROKER_ERROR")) is None
    assert broker.detect_quota_response(root, request, dict(response, exit_code=0)) is None
    assert broker.detect_quota_response(root, request, dict(response, exit_code=2)) is None
    assert broker.detect_quota_response(root, request, dict(response, stderr="err\n")) is None
    p_st = dict(payload, state="READY_FOR_CODEX")
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_st)) is None

    # Wrong status state
    p_st_state = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], status_state="PROPOSAL_READY")
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_st_state)) is None

    # Oversize stdout
    assert broker.detect_quota_response(
        root, request, dict(response, stdout=" " * (broker.MAX_QUOTA_STDOUT_BYTES + 1))
    ) is None

    # Bogus KNOWN / UNKNOWN
    for bad_kind, bad_reset in [
        ("BOGUS", None),
        ("QUOTA_RESET_KNOWN", None),
        ("QUOTA_RESET_UNKNOWN", "2099-01-01T00:00:00Z"),
    ]:
        bad_ev = dict(payload["quota_evidence"], kind=bad_kind, reset_at_utc=bad_reset)
        p_ev = dict(payload, quota_evidence=bad_ev)
        assert broker.detect_quota_response(root, request, _make_quota_response(request, p_ev)) is None

    # Model / provider / conversation / timezone
    bad_prov = dict(payload, quota_evidence=dict(payload["quota_evidence"], provider="openai"))
    assert broker.detect_quota_response(root, request, _make_quota_response(request, bad_prov)) is None

    # Wrong model for worker / verifier / correction worker
    bad_mod = dict(payload, quota_evidence=dict(payload["quota_evidence"], model=broker.MODEL))
    assert broker.detect_quota_response(root, request, _make_quota_response(request, bad_mod)) is None
    p_st_mod = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], role="worker", is_correction=True, status_model=broker.MODEL)
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_st_mod)) is None
    p_ver_bad_mod = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], role="verifier", status_model=broker.WORKER_MODEL)
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_ver_bad_mod)) is None

    # Conversation ID variations
    for bad_cid in ["", "conv with space", "conv\nline", "a" * 129]:
        bad_conv = dict(payload, quota_evidence=dict(payload["quota_evidence"], conversation_id=bad_cid))
        assert broker.detect_quota_response(root, request, _make_quota_response(request, bad_conv)) is None

    # Status conversation mismatch
    p_bad_cid = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], conversation_id="conv-orig")
    p_bad_cid["quota_evidence"]["conversation_id"] = "conv-mismatch"
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_bad_cid)) is None

    # Timezone variations
    for bad_tz in ["2099-01-01T00:00:00+01:00", "not-a-date", "2099-01-01 00:00:00Z"]:
        bad_ts = dict(payload, quota_evidence=dict(payload["quota_evidence"], reset_at_utc=bad_tz))
        assert broker.detect_quota_response(root, request, _make_quota_response(request, bad_ts)) is None

    # Extra and missing keys in payload (including old 16-key payload)
    for missing_k in ["changed_paths", "blockers", "task_id", "quota_attempt"]:
        bad_p = dict(payload)
        del bad_p[missing_k]
        assert broker.detect_quota_response(root, request, _make_quota_response(request, bad_p)) is None
    bad_p_extra = dict(payload, extra_field=1)
    assert broker.detect_quota_response(root, request, _make_quota_response(request, bad_p_extra)) is None

    for missing_ev_k in ["kind", "provider", "model", "conversation_id", "reset_at_utc"]:
        bad_ev = dict(payload["quota_evidence"])
        del bad_ev[missing_ev_k]
        p_missing_ev = dict(payload, quota_evidence=bad_ev)
        assert broker.detect_quota_response(root, request, _make_quota_response(request, p_missing_ev)) is None
    bad_ev_extra = dict(payload, quota_evidence=dict(payload["quota_evidence"], extra_key="extra"))
    assert broker.detect_quota_response(root, request, _make_quota_response(request, bad_ev_extra)) is None

    # quota_attempt validation: missing, extra, malformed keys
    p_no_attempt_sha = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], omit_attempt_key="status_sha256")
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_no_attempt_sha)) is None
    p_extra_attempt = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], extra_attempt_keys={"extra": 1})
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_extra_attempt)) is None
    p_str_attempt = dict(payload, quota_attempt="not-a-dict")
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_str_attempt)) is None

    # Forged status digest
    p_forged_sha = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], corrupt_status_sha=True)
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_forged_sha)) is None

    # Changed status bytes after digest
    p_changed = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], run_id=f"{request['run_prefix']}-worker-0")
    status_file = root / ".local/two-site-refactor/proposals" / p_changed["quota_attempt"]["run_id"] / "status.json"
    status_file.write_bytes(status_file.read_bytes() + b" ")
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_changed)) is None

    # Duplicate JSON in status
    dup_status_bytes = b'{"state":"BLOCKED","state":"BLOCKED","run_id":"' + p_changed["quota_attempt"]["run_id"].encode("utf-8") + b'"}'
    p_dup_st = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], run_id=f"{request['run_prefix']}-worker-2", status_raw_content=dup_status_bytes)
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_dup_st)) is None

    # Symlinked status
    p_sym = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], run_id=f"{request['run_prefix']}-worker-0-retry-1")
    st_path = root / ".local/two-site-refactor/proposals" / p_sym["quota_attempt"]["run_id"] / "status.json"
    real_st = st_path.with_name("real_status.json")
    os.rename(st_path, real_st)
    os.symlink(real_st, st_path)
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_sym)) is None

    # Hardlinked status
    st_path.unlink()
    os.link(real_st, st_path)
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_sym)) is None

    # Oversize status
    oversize_bytes = b"{" + b" " * (broker.MAX_CONTEXT + 1) + b"}"
    p_big = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], run_id=f"{request['run_prefix']}-worker-0-retry-2", status_raw_content=oversize_bytes)
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_big)) is None

    # Existing result.json or proposal.patch in proposals dir
    p_with_result = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], run_id=f"{request['run_prefix']}-verifier-0", role="verifier", create_result_file=True)
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_with_result)) is None
    p_with_patch = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], run_id=f"{request['run_prefix']}-verifier-1", role="verifier", create_patch_file=True)
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_with_patch)) is None

    # Wrong role / run / retry bindings
    p_bad_role = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"])
    p_bad_role["quota_attempt"]["role"] = "reviewer"
    assert broker.detect_quota_response(root, request, _make_quota_response(request, p_bad_role)) is None

    for bad_run in [
        "wrong-prefix-worker-0",
        f"{request['run_prefix']}-worker-3",
        f"{request['run_prefix']}-worker-0-retry-3",
        f"{request['run_prefix']}-verifier-0",  # attempt role worker but run is verifier
    ]:
        p_bad_run = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"], run_id=bad_run)
        assert broker.detect_quota_response(root, request, _make_quota_response(request, p_bad_run)) is None


def test_detect_quota_response_link_unsafe_and_guarded_paths(
    broker_repo: tuple[Path, Path, AssignmentScope, dict[str, str]],
) -> None:
    root, task, _scope, environment = broker_repo
    request = make_valid_request(root, task, environment)
    payload = _make_quota_payload(root, "BROKER-TEST-1", request["source_head"])
    response = _make_quota_response(request, payload)

    # 1. Outside task directory
    outside_req = dict(request, task_path="source.py")
    assert broker.detect_quota_response(root, outside_req, response) is None

    # 2. Hardlinked task
    hardlink_task = task.with_name("task_hl.json")
    os.link(task, hardlink_task)
    hl_req = dict(request, task_path=hardlink_task.relative_to(root).as_posix())
    assert broker.detect_quota_response(root, hl_req, response) is None

    # 3. Symlinked task
    symlink_task = task.with_name("task_sym.json")
    symlink_task.symlink_to(task)
    sym_req = dict(request, task_path=symlink_task.relative_to(root).as_posix())
    assert broker.detect_quota_response(root, sym_req, response) is None

    # 4. Nonexistent task
    missing_req = dict(request, task_path=".local/two-site-refactor/tasks/missing.json")
    assert broker.detect_quota_response(root, missing_req, response) is None
