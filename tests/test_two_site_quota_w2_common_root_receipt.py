"""Hermetic same-root Broker cycle reservation prerequisite for W2 receipt."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_codex_autopilot import (
    PROTECTED_REFS,
    exclusive_supervisor_lock,
    validate_accepted_repository,
    validate_repository,
)
from scripts.two_site_provider_broker import (
    BrokerError,
    publish_cycle_reservation,
    read_cycle_reservation,
    validate_request,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_provider_broker_cycle_reservation_read import (
    _setup_cycle_root,
    _write_startup_json,
)
from tests.test_two_site_quota_full_tree_repository_facts import _populate_full_tree_repo
from tests.test_two_site_quota_join_semantic_fixture import (
    setup_two_site_quota_join_semantic_fixture,
)


def setup_two_site_quota_w2_common_root_reservation(
    root: Path,
    task: Path,
    original_scope: Any,
    original_environment: dict[str, str],
    *,
    publish: bool = False,
) -> dict[str, Any]:
    """Build hermetic root and prepare or publish cycle reservation for W2 receipt."""
    _populate_full_tree_repo(root)
    join_fixture = setup_two_site_quota_join_semantic_fixture(
        root, task, original_scope, original_environment
    )
    head = join_fixture["head"]
    for ref in PROTECTED_REFS:
        subprocess.run(["git", "-C", str(root), "update-ref", ref, head], check=True)

    request = join_fixture["request"]
    validated_req, validated_task_path, validated_scope = validate_request(root, request)
    facts = validate_repository(root)
    accepted_facts = validate_accepted_repository(facts)

    expected_refs = {ref: head for ref in sorted(PROTECTED_REFS)}
    assert accepted_facts["protected_refs"] == expected_refs

    task_bytes = task.read_bytes()
    task_sha = hashlib.sha256(task_bytes).hexdigest()
    assert request["task_sha256"] == task_sha

    controller_run_id = request["controller_run_id"]
    _, runs_dir = _setup_cycle_root(str(root), controller_run_id)
    startup_path = _write_startup_json(
        runs_dir,
        cycle_id=controller_run_id,
        source_head=request["source_head"],
        assignment_sha256=request["assignment_sha256"],
    )

    lock_path = root / ".local" / "two-site-refactor" / "autopilot" / "supervisor.lock"

    fixture: dict[str, Any] = {
        "root": root,
        "task": task,
        "head": head,
        "actual_scope": join_fixture["actual_scope"],
        "request": request,
        "controller_run_id": controller_run_id,
        "runs_dir": runs_dir,
        "startup_path": startup_path,
        "lock_path": lock_path,
        "facts": facts,
        "accepted_facts": accepted_facts,
        "validated_req": validated_req,
        "validated_task_path": validated_task_path,
        "validated_scope": validated_scope,
        "task_bytes": task_bytes,
        "task_sha": task_sha,
        "published": None,
    }

    if publish:
        with exclusive_supervisor_lock(lock_path):
            fixture["published"] = publish_cycle_reservation(root, request)

    return fixture


setup_two_site_quota_w2_common_root_receipt = setup_two_site_quota_w2_common_root_reservation


def test_two_site_quota_w2_common_root_reservation(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    root, task, original_scope, original_environment = broker_repo
    fixture = setup_two_site_quota_w2_common_root_reservation(
        root, task, original_scope, original_environment, publish=False
    )
    root = fixture["root"]
    task = fixture["task"]
    head = fixture["head"]
    request = fixture["request"]
    actual_scope = fixture["actual_scope"]
    controller_run_id = fixture["controller_run_id"]
    runs_dir = fixture["runs_dir"]
    lock_path = fixture["lock_path"]
    accepted_facts = fixture["accepted_facts"]
    task_bytes = fixture["task_bytes"]
    task_sha = fixture["task_sha"]

    expected_protected_refs = {ref: head for ref in sorted(PROTECTED_REFS)}
    assert accepted_facts["protected_refs"] == expected_protected_refs
    assert request["source_head"] == head
    assert request["assignment_id"] == actual_scope.assignment_id
    assert request["assignment_sha256"] == actual_scope.sha256
    assert request["task_sha256"] == task_sha

    with exclusive_supervisor_lock(lock_path):
        published = publish_cycle_reservation(root, request)

        expected_reservation = {
            "controller_run_id": request["controller_run_id"],
            "run_prefix": request["run_prefix"],
            "task_path": request["task_path"],
            "task_sha256": request["task_sha256"],
            "source_head": request["source_head"],
            "assignment_id": request["assignment_id"],
            "assignment_sha256": request["assignment_sha256"],
        }
        assert published["reservation"] == expected_reservation
        assert set(published["reservation"].keys()) == {
            "controller_run_id",
            "run_prefix",
            "task_path",
            "task_sha256",
            "source_head",
            "assignment_id",
            "assignment_sha256",
        }

        expected_request_sha256 = hashlib.sha256(
            json.dumps(
                request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        assert published["request_sha256"] == expected_request_sha256

        read_record = read_cycle_reservation(root, controller_run_id)
        assert read_record == published

        assert read_record["controller_run_id"] == controller_run_id
        assert read_record["reservation"]["controller_run_id"] == controller_run_id
        assert read_record["reservation"]["source_head"] == head
        assert read_record["reservation"]["source_head"] == request["source_head"]
        assert read_record["reservation"]["assignment_id"] == actual_scope.assignment_id
        assert read_record["reservation"]["assignment_sha256"] == actual_scope.sha256
        assert read_record["reservation"]["run_prefix"] == request["run_prefix"]
        assert read_record["reservation"]["task_sha256"] == task_sha
        assert read_record["request_id"] == request["request_id"]
        assert read_record["created_at"] == request["created_at"]

        reservation_file = runs_dir / "broker-reservation.json"
        assert reservation_file.is_file()
        reservation_bytes_snapshot = reservation_file.read_bytes()
        task_bytes_snapshot = task.read_bytes()
        assert task_bytes_snapshot == task_bytes

        with pytest.raises(BrokerError, match="broker reservation target already exists"):
            publish_cycle_reservation(root, request)

        assert reservation_file.read_bytes() == reservation_bytes_snapshot
        assert task.read_bytes() == task_bytes_snapshot
        assert sorted(os.listdir(runs_dir)) == [
            "broker-reservation.json",
            "preparation-startup.json",
        ]
