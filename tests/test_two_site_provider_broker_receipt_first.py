#!/usr/bin/env python3
"""Hermetic unit tests for AE03 C2 receipt-first terminal custody in provider broker."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import two_site_provider_broker as broker
from tests.test_two_site_provider_broker import (
    _make_quota_payload,
    _make_quota_response,
    broker_repo,
    make_valid_request,
)


def test_positive_serve_genuine_quota_response_receipt_first(
    broker_repo: tuple[Path, Path, object, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, task, scope, environment = broker_repo
    request = make_valid_request(root, task, environment)
    req_id = request["request_id"]
    task_id = json.loads(task.read_text(encoding="utf-8"))["task_id"]
    payload = _make_quota_payload(root, task_id, request["source_head"], run_prefix=request["run_prefix"])
    quota_response = _make_quota_response(request, payload)

    state_root, status_path, requests, claimed, responses = broker.broker_paths(root)
    terminal_quota = state_root / "terminal-quota"
    source = requests / f"{req_id}.json"
    broker._atomic_new_json(source, request, broker.MAX_REQUEST_BYTES)

    events: list[tuple[str, str]] = []

    orig_fsync = broker.fsync_private_record_directory
    def spy_fsync(path: Path) -> None:
        if path == claimed:
            events.append(("fsync", "claimed"))
        elif path == requests:
            events.append(("fsync", "requests"))
        elif path == terminal_quota:
            events.append(("fsync", "terminal-quota"))
        else:
            events.append(("fsync", path.name))
        orig_fsync(path)
    monkeypatch.setattr(broker, "fsync_private_record_directory", spy_fsync)

    orig_atomic = broker._atomic_new_json
    def spy_atomic(path: Path, value: broker.Mapping[str, object], limit: int) -> None:
        if "terminal-quota" in str(path):
            events.append(("write_receipt", path.name))
        elif "responses" in str(path):
            events.append(("write_response", path.name))
        orig_atomic(path, value, limit)
    monkeypatch.setattr(broker, "_atomic_new_json", spy_atomic)

    orig_unlink = Path.unlink
    def spy_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if "claimed" in str(self):
            events.append(("unlink_claim", self.name))
        orig_unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", spy_unlink)

    status_calls: list[dict] = []
    orig_write_status = broker._write_status
    def spy_write_status(path: Path, state: str, **fields: object) -> None:
        if path == status_path:
            status_calls.append({"state": state, **fields})
            events.append(("status", state))
        orig_write_status(path, state, **fields)
    monkeypatch.setattr(broker, "_write_status", spy_write_status)

    executor_calls: list[str] = []
    def fake_executor(r: Path, req: dict, t_path: Path, sc: broker.AssignmentScope) -> dict:
        executor_calls.append(req["request_id"])
        events.append(("executor", req["request_id"]))
        return quota_response

    rc = broker.serve(
        root=root,
        once=True,
        executor=fake_executor,
        scope_loader=lambda _r: scope,
        install_signal_handlers=False,
    )

    assert rc == 0
    assert executor_calls == [req_id]

    expected_sequence = [
        ("status", "IDLE"),
        ("fsync", "claimed"),
        ("fsync", "requests"),
        ("status", "RUNNING"),
        ("executor", req_id),
        ("write_receipt", f"{req_id}.json"),
        ("fsync", "terminal-quota"),
        ("write_response", f"{req_id}.json"),
        ("unlink_claim", f"{req_id}.json"),
        ("status", "IDLE"),
        ("status", "STOPPED"),
    ]
    filtered_events = [
        e for e in events if e[0] in {"fsync", "executor", "write_receipt", "write_response", "unlink_claim", "status"}
    ]
    assert filtered_events == expected_sequence

    idle_status = [s for s in status_calls if s["state"] == "IDLE" and "last_request_id" in s]
    assert len(idle_status) == 1
    assert idle_status[0]["last_request_id"] == req_id
    assert idle_status[0]["last_exit_code"] == 1

    read_receipt = broker.read_terminal_quota_record(root, req_id)
    assert read_receipt["request"] == request
    assert read_receipt["response"] == quota_response
    assert read_receipt["quota_evidence"] is not None

    assert (responses / f"{req_id}.json").is_file()
    saved_response = broker._read_json_file(root, responses / f"{req_id}.json", broker.MAX_RESPONSE_BYTES, "resp")
    assert saved_response == quota_response

    assert not (claimed / f"{req_id}.json").exists()
    assert not source.exists()

    status_doc = broker._read_broker_status(root, status_path)
    assert status_doc["state"] == "STOPPED"


def test_serve_fault_publisher_before_publication_leaves_claim(
    broker_repo: tuple[Path, Path, object, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, task, scope, environment = broker_repo
    request = make_valid_request(root, task, environment)
    req_id = request["request_id"]
    task_id = json.loads(task.read_text(encoding="utf-8"))["task_id"]
    payload = _make_quota_payload(root, task_id, request["source_head"], run_prefix=request["run_prefix"])
    quota_response = _make_quota_response(request, payload)

    state_root, status_path, requests, claimed, responses = broker.broker_paths(root)
    terminal_quota = state_root / "terminal-quota"
    source = requests / f"{req_id}.json"
    broker._atomic_new_json(source, request, broker.MAX_REQUEST_BYTES)

    fsynced_dirs: list[Path] = []
    orig_fsync = broker.fsync_private_record_directory
    def spy_fsync(path: Path) -> None:
        fsynced_dirs.append(path)
        orig_fsync(path)
    monkeypatch.setattr(broker, "fsync_private_record_directory", spy_fsync)

    def failing_publish(r: Path, req: dict, resp: dict) -> dict:
        raise broker.BrokerError("injected publisher fault before publication")
    monkeypatch.setattr(broker, "publish_terminal_quota_record", failing_publish)

    executor_calls: list[str] = []
    def fake_executor(r: Path, req: dict, t_path: Path, sc: broker.AssignmentScope) -> dict:
        executor_calls.append(req["request_id"])
        return quota_response

    with pytest.raises(broker.BrokerError, match="injected publisher fault before publication"):
        broker.serve(
            root=root,
            once=True,
            executor=fake_executor,
            scope_loader=lambda _r: scope,
            install_signal_handlers=False,
        )

    assert executor_calls == [req_id]
    assert (claimed / f"{req_id}.json").is_file()
    assert not (responses / f"{req_id}.json").exists()
    assert not (terminal_quota / f"{req_id}.json").exists()
    assert fsynced_dirs == [claimed, requests]
    status_doc = broker._read_broker_status(root, status_path)
    assert status_doc["state"] == "STOPPED"


def test_serve_fault_publisher_fsync_leaves_claim(
    broker_repo: tuple[Path, Path, object, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, task, scope, environment = broker_repo
    request = make_valid_request(root, task, environment)
    req_id = request["request_id"]
    task_id = json.loads(task.read_text(encoding="utf-8"))["task_id"]
    payload = _make_quota_payload(root, task_id, request["source_head"], run_prefix=request["run_prefix"])
    quota_response = _make_quota_response(request, payload)

    state_root, status_path, requests, claimed, responses = broker.broker_paths(root)
    terminal_quota = state_root / "terminal-quota"
    source = requests / f"{req_id}.json"
    broker._atomic_new_json(source, request, broker.MAX_REQUEST_BYTES)

    fsynced_dirs: list[Path] = []
    orig_fsync = broker.fsync_private_record_directory
    def failing_fsync(path: Path) -> None:
        fsynced_dirs.append(path)
        if path == terminal_quota:
            raise broker.BrokerError("injected terminal-quota fsync failure")
        orig_fsync(path)
    monkeypatch.setattr(broker, "fsync_private_record_directory", failing_fsync)

    executor_calls: list[str] = []
    def fake_executor(r: Path, req: dict, t_path: Path, sc: broker.AssignmentScope) -> dict:
        executor_calls.append(req["request_id"])
        return quota_response

    with pytest.raises(broker.BrokerError, match="injected terminal-quota fsync failure"):
        broker.serve(
            root=root,
            once=True,
            executor=fake_executor,
            scope_loader=lambda _r: scope,
            install_signal_handlers=False,
        )

    assert executor_calls == [req_id]
    assert (claimed / f"{req_id}.json").is_file()
    assert not (responses / f"{req_id}.json").exists()
    assert (terminal_quota / f"{req_id}.json").is_file()
    assert fsynced_dirs == [claimed, requests, terminal_quota]
    status_doc = broker._read_broker_status(root, status_path)
    assert status_doc["state"] == "STOPPED"


def test_serve_fault_response_write_after_receipt_leaves_valid_receipt_and_claim(
    broker_repo: tuple[Path, Path, object, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, task, scope, environment = broker_repo
    request = make_valid_request(root, task, environment)
    req_id = request["request_id"]
    task_id = json.loads(task.read_text(encoding="utf-8"))["task_id"]
    payload = _make_quota_payload(root, task_id, request["source_head"], run_prefix=request["run_prefix"])
    quota_response = _make_quota_response(request, payload)

    state_root, status_path, requests, claimed, responses = broker.broker_paths(root)
    terminal_quota = state_root / "terminal-quota"
    source = requests / f"{req_id}.json"
    broker._atomic_new_json(source, request, broker.MAX_REQUEST_BYTES)

    fsynced_dirs: list[Path] = []
    orig_fsync = broker.fsync_private_record_directory
    def spy_fsync(path: Path) -> None:
        fsynced_dirs.append(path)
        orig_fsync(path)
    monkeypatch.setattr(broker, "fsync_private_record_directory", spy_fsync)

    response_target = responses / f"{req_id}.json"
    orig_atomic = broker._atomic_new_json
    def failing_atomic(path: Path, value: broker.Mapping[str, object], limit: int) -> None:
        if path == response_target:
            raise broker.BrokerError("injected response write failure")
        orig_atomic(path, value, limit)
    monkeypatch.setattr(broker, "_atomic_new_json", failing_atomic)

    executor_calls: list[str] = []
    def fake_executor(r: Path, req: dict, t_path: Path, sc: broker.AssignmentScope) -> dict:
        executor_calls.append(req["request_id"])
        return quota_response

    with pytest.raises(broker.BrokerError, match="injected response write failure"):
        broker.serve(
            root=root,
            once=True,
            executor=fake_executor,
            scope_loader=lambda _r: scope,
            install_signal_handlers=False,
        )

    assert executor_calls == [req_id]
    assert (claimed / f"{req_id}.json").is_file()
    assert not (responses / f"{req_id}.json").exists()
    assert fsynced_dirs == [claimed, requests, terminal_quota]

    receipt = broker.read_terminal_quota_record(root, req_id)
    assert receipt["request"] == request
    assert receipt["response"] == quota_response
    assert receipt["quota_evidence"] is not None

    status_doc = broker._read_broker_status(root, status_path)
    assert status_doc["state"] == "STOPPED"


def test_serve_duplicate_receipt_collision_escapes_conversion_and_leaves_claim(
    broker_repo: tuple[Path, Path, object, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, task, scope, environment = broker_repo
    request = make_valid_request(root, task, environment)
    req_id = request["request_id"]
    task_id = json.loads(task.read_text(encoding="utf-8"))["task_id"]
    payload = _make_quota_payload(root, task_id, request["source_head"], run_prefix=request["run_prefix"])
    quota_response = _make_quota_response(request, payload)

    state_root, status_path, requests, claimed, responses = broker.broker_paths(root)
    source = requests / f"{req_id}.json"
    broker._atomic_new_json(source, request, broker.MAX_REQUEST_BYTES)

    fsynced_dirs: list[Path] = []
    orig_fsync = broker.fsync_private_record_directory
    def spy_fsync(path: Path) -> None:
        fsynced_dirs.append(path)
        orig_fsync(path)
    monkeypatch.setattr(broker, "fsync_private_record_directory", spy_fsync)

    executor_calls: list[str] = []
    def colliding_executor(r: Path, req: dict, t_path: Path, sc: broker.AssignmentScope) -> dict:
        executor_calls.append(req["request_id"])
        terminal_quota = broker._terminal_quota_directory(root)
        collision_path = terminal_quota / f"{req_id}.json"
        broker._atomic_new_json(collision_path, {"collided": True}, broker.MAX_RECORD_BYTES)
        return quota_response

    with pytest.raises(broker.BrokerError, match="terminal quota record already exists"):
        broker.serve(
            root=root,
            once=True,
            executor=colliding_executor,
            scope_loader=lambda _r: scope,
            install_signal_handlers=False,
        )

    assert executor_calls == [req_id]
    assert (claimed / f"{req_id}.json").is_file()
    assert not (responses / f"{req_id}.json").exists()
    assert fsynced_dirs == [claimed, requests]
    status_doc = broker._read_broker_status(root, status_path)
    assert status_doc["state"] == "STOPPED"


def test_serve_nonquota_response_leaves_no_terminal_receipt(
    broker_repo: tuple[Path, Path, object, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, task, scope, environment = broker_repo
    request = make_valid_request(root, task, environment)
    req_id = request["request_id"]

    state_root, status_path, requests, claimed, responses = broker.broker_paths(root)
    terminal_quota = state_root / "terminal-quota"
    source = requests / f"{req_id}.json"
    broker._atomic_new_json(source, request, broker.MAX_REQUEST_BYTES)

    fsynced_dirs: list[Path] = []
    orig_fsync = broker.fsync_private_record_directory
    def spy_fsync(path: Path) -> None:
        fsynced_dirs.append(path)
        orig_fsync(path)
    monkeypatch.setattr(broker, "fsync_private_record_directory", spy_fsync)

    status_calls: list[dict] = []
    orig_write_status = broker._write_status
    def spy_write_status(path: Path, state: str, **fields: object) -> None:
        if path == status_path:
            status_calls.append({"state": state, **fields})
        orig_write_status(path, state, **fields)
    monkeypatch.setattr(broker, "_write_status", spy_write_status)

    normal_response = {
        "schema_version": "1.0",
        "request_id": req_id,
        "run_prefix": request["run_prefix"],
        "source_head": request["source_head"],
        "state": "COMPLETED",
        "exit_code": 0,
        "stdout": json.dumps({"state": "COMPLETED", "result": "ok"}),
        "stderr": "",
        "finished_at": time.time(),
    }

    executor_calls: list[str] = []
    def fake_executor(r: Path, req: dict, t_path: Path, sc: broker.AssignmentScope) -> dict:
        executor_calls.append(req["request_id"])
        return normal_response

    rc = broker.serve(
        root=root,
        once=True,
        executor=fake_executor,
        scope_loader=lambda _r: scope,
        install_signal_handlers=False,
    )

    assert rc == 0
    assert executor_calls == [req_id]
    assert not os.path.lexists(terminal_quota / f"{req_id}.json")
    assert not (claimed / f"{req_id}.json").exists()
    assert (responses / f"{req_id}.json").is_file()
    assert fsynced_dirs == [claimed, requests]

    saved_response = broker._read_json_file(root, responses / f"{req_id}.json", broker.MAX_RESPONSE_BYTES, "resp")
    assert saved_response == normal_response

    idle_status = [s for s in status_calls if s["state"] == "IDLE" and "last_request_id" in s]
    assert len(idle_status) == 1
    assert idle_status[0]["last_request_id"] == req_id
    assert idle_status[0]["last_exit_code"] == 0

    status_doc = broker._read_broker_status(root, status_path)
    assert status_doc["state"] == "STOPPED"
