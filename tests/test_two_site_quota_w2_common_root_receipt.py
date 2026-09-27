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
from scripts.two_site_proposal_runner import load_task
from scripts.two_site_provider_broker import (
    BrokerError,
    publish_cycle_reservation,
    publish_terminal_quota_record,
    read_cycle_reservation,
    read_terminal_quota_bytes,
    read_terminal_quota_record,
    validate_request,
)
from scripts.two_site_quota_resume_worker_two_file_reader import (
    read_and_bind_worker_two_result,
)
from scripts.two_site_semantic_attempts import (
    extract_semantic_identity,
    read_existing_semantic_attempt,
)
from tests.test_two_site_provider_broker import (
    _make_quota_payload,
    _make_quota_response,
    broker_repo,
)
from tests.test_two_site_provider_broker_cycle_reservation_read import (
    _setup_cycle_root,
    _write_startup_json,
)
from tests.test_two_site_quota_full_tree_repository_facts import _populate_full_tree_repo
from tests.test_two_site_quota_join_semantic_fixture import (
    setup_two_site_quota_join_semantic_fixture,
)
from tests.test_two_site_quota_resume_worker_two_fileproof import (
    _setup_materialized_worker_two_env,
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


def test_two_site_quota_w2_common_root_receipt(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    root, task, original_scope, original_environment = broker_repo
    fixture = setup_two_site_quota_w2_common_root_reservation(
        root, task, original_scope, original_environment, publish=True
    )
    published_reservation = fixture["published"]
    assert published_reservation is not None
    head = fixture["head"]
    request = fixture["request"]
    run_prefix = request["run_prefix"]

    task_result = load_task(root, task)
    real_task = task_result[0] if isinstance(task_result, tuple) else task_result
    task_id = real_task["task_id"]

    identity = extract_semantic_identity(real_task)
    persisted_attempt = read_existing_semantic_attempt(
        root,
        root / ".local" / "two-site-refactor",
        real_task,
        run_prefix,
    )
    assert persisted_attempt["fingerprint"] == identity["fingerprint"]
    assert persisted_attempt["task_id"] == task_id
    assert persisted_attempt["run_prefix"] == run_prefix

    _env_root, ctx, returned_state, meta = _setup_materialized_worker_two_env(
        root,
        task_data=real_task,
        source_head=request["source_head"],
        admitted_task_path=task,
        original_run_prefix=run_prefix,
    )

    bound_w2 = read_and_bind_worker_two_result(root, ctx, returned_state)
    assert bound_w2["returned_state"]["state"] == "WORKER_PROPOSAL_READY"
    assert bound_w2["returned_state"]["final_worker_run_id"] == meta["w2_id"]
    assert bound_w2["returned_state"]["proposal_sha256"] == meta["w2_sha"]
    assert bound_w2["current_worker"] == (
        meta["w2_id"],
        meta["w2_sha"],
        meta["w2_result_text"],
        meta["w2_conv_id"],
    )

    w1_status_path = (
        root / ".local" / "two-site-refactor" / "proposals" / meta["w1_id"] / "status.json"
    )
    r1_status_path = (
        root / ".local" / "two-site-refactor" / "proposals" / meta["r1_id"] / "status.json"
    )
    w1_status_bytes = w1_status_path.read_bytes()
    r1_status_bytes = r1_status_path.read_bytes()
    task_bytes = task.read_bytes()
    task_sha = hashlib.sha256(task_bytes).hexdigest()

    orig_w2_id = f"{run_prefix}-worker-2"
    assert orig_w2_id != meta["w2_id"]

    payload = _make_quota_payload(
        root,
        task_id,
        head,
        run_prefix=run_prefix,
        is_correction=True,
        run_id=orig_w2_id,
    )

    receipt1_rel = (
        f".local/two-site-refactor/pipeline/{run_prefix}/pre-review-tests-1.json"
    )
    receipt1_path = root / receipt1_rel
    receipt1_bytes = receipt1_path.read_bytes()
    receipt1_sha = hashlib.sha256(receipt1_bytes).hexdigest()

    orig_w2_status_path = (
        root / ".local" / "two-site-refactor" / "proposals" / orig_w2_id / "status.json"
    )
    orig_w2_status_bytes = orig_w2_status_path.read_bytes()
    orig_w2_status_sha = hashlib.sha256(orig_w2_status_bytes).hexdigest()

    payload["final_worker_run_id"] = meta["w1_id"]
    payload["final_reviewer_run_id"] = meta["r1_id"]
    payload["proposal_sha256"] = meta["w1_sha"]
    payload["proposal_path"] = (
        f".local/two-site-refactor/proposals/{meta['w1_id']}/result.json"
    )
    payload["reviewer_path"] = (
        f".local/two-site-refactor/proposals/{meta['r1_id']}/result.json"
    )
    payload["pre_review_test_path"] = receipt1_rel
    payload["pre_review_test_sha256"] = receipt1_sha
    payload["pre_review_test_state"] = "PASSED"
    payload["changed_paths"] = ["tests/test_new.py"]
    payload["semantic_task_fingerprint"] = identity["fingerprint"]
    payload["quota_attempt"]["status_sha256"] = orig_w2_status_sha

    assert payload["correction_count"] == 1
    assert payload["quota_attempt"]["role"] == "worker"
    assert payload["quota_attempt"]["run_id"] == orig_w2_id
    assert payload["quota_attempt"]["status_sha256"] == orig_w2_status_sha

    response = _make_quota_response(request, payload)
    published_receipt = publish_terminal_quota_record(root, request, response)
    read_receipt = read_terminal_quota_record(root, request["request_id"])
    raw_receipt_bytes = read_terminal_quota_bytes(root, request["request_id"])

    canonical_request_bytes = json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    expected_request_sha = hashlib.sha256(canonical_request_bytes).hexdigest()

    assert read_receipt["request_sha256"] == published_reservation["request_sha256"]
    assert read_receipt["request_sha256"] == expected_request_sha
    assert published_receipt["request_sha256"] == published_reservation["request_sha256"]
    assert read_receipt["request"] == request
    assert published_receipt["request"] == request

    canonical_published_bytes = json.dumps(
        published_receipt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert raw_receipt_bytes == canonical_published_bytes

    decoded_stdout = json.loads(read_receipt["response"]["stdout"])
    assert decoded_stdout["state"] == "TASK_BLOCKED"
    assert decoded_stdout["task_id"] == task_id
    assert decoded_stdout["source_head"] == head
    assert decoded_stdout["final_worker_run_id"] == meta["w1_id"]
    assert decoded_stdout["final_reviewer_run_id"] == meta["r1_id"]
    assert decoded_stdout["proposal_sha256"] == meta["w1_sha"]
    assert (
        decoded_stdout["proposal_path"]
        == f".local/two-site-refactor/proposals/{meta['w1_id']}/result.json"
    )
    assert (
        decoded_stdout["reviewer_path"]
        == f".local/two-site-refactor/proposals/{meta['r1_id']}/result.json"
    )
    assert decoded_stdout["pre_review_test_path"] == receipt1_rel
    assert decoded_stdout["pre_review_test_sha256"] == receipt1_sha
    assert decoded_stdout["pre_review_test_state"] == "PASSED"
    assert decoded_stdout["changed_paths"] == ["tests/test_new.py"]
    assert decoded_stdout["semantic_task_fingerprint"] == identity["fingerprint"]
    assert decoded_stdout["correction_count"] == 1
    assert decoded_stdout["blockers"] == ["observational quota, no replay authority"]

    assert (root / decoded_stdout["proposal_path"]).is_file()
    assert (root / decoded_stdout["reviewer_path"]).is_file()
    assert (root / decoded_stdout["pre_review_test_path"]).is_file()
    assert (
        hashlib.sha256((root / decoded_stdout["proposal_path"]).read_bytes()).hexdigest()
        == meta["w1_sha"]
    )
    assert (
        hashlib.sha256(
            (root / decoded_stdout["pre_review_test_path"]).read_bytes()
        ).hexdigest()
        == receipt1_sha
    )

    assert decoded_stdout["quota_attempt"]["role"] == "worker"
    assert decoded_stdout["quota_attempt"]["run_id"] == orig_w2_id
    assert decoded_stdout["quota_attempt"]["status_sha256"] == orig_w2_status_sha
    assert (
        decoded_stdout["quota_attempt"]["status_sha256"]
        == hashlib.sha256(orig_w2_status_path.read_bytes()).hexdigest()
    )
    assert read_receipt["quota_evidence"] == payload["quota_evidence"]

    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    assert not (proposals_dir / orig_w2_id / "result.json").exists()
    assert (proposals_dir / meta["w2_id"] / "result.json").exists()

    w0_id = f"{run_prefix}-worker-0"
    r0_id = f"{run_prefix}-reviewer-0"
    for run_id in (w0_id, r0_id, meta["w1_id"], meta["r1_id"], meta["w2_id"]):
        run_st = json.loads((proposals_dir / run_id / "status.json").read_text(encoding="utf-8"))
        assert run_st["head"] == head
    orig_w2_st = json.loads(orig_w2_status_path.read_text(encoding="utf-8"))
    assert orig_w2_st["head"] == head

    assert task.read_bytes() == task_bytes
    assert hashlib.sha256(task.read_bytes()).hexdigest() == task_sha
    assert w1_status_path.read_bytes() == w1_status_bytes
    assert r1_status_path.read_bytes() == r1_status_bytes

    tampered_status = json.loads(orig_w2_status_path.read_text(encoding="utf-8"))
    tampered_status["drifted"] = True
    tampered_status["state"] = "CORRUPTED"
    orig_w2_status_path.write_text(json.dumps(tampered_status), encoding="utf-8")

    with pytest.raises(BrokerError, match="terminal quota response not detected"):
        read_terminal_quota_record(root, request["request_id"])

    assert task.read_bytes() == task_bytes
    assert hashlib.sha256(task.read_bytes()).hexdigest() == task_sha
    assert w1_status_path.read_bytes() == w1_status_bytes
    assert r1_status_path.read_bytes() == r1_status_bytes
    assert read_terminal_quota_bytes(root, request["request_id"]) == raw_receipt_bytes

    expected_runs = {
        w0_id,
        r0_id,
        meta["w1_id"],
        meta["r1_id"],
        meta["w2_id"],
        orig_w2_id,
    }
    assert set(os.listdir(proposals_dir)) == expected_runs
    assert not (proposals_dir / orig_w2_id / "result.json").exists()
