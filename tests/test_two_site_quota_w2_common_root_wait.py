"""Hermetic same-root Broker reservation to initial quota wait admission proof."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_codex_autopilot import (
    ensure_rescope_epoch_dir,
    exclusive_supervisor_lock,
    publish_rescope_intent,
    rescope_epoch_basename,
)
from scripts.two_site_proposal_runner import load_task
from scripts.two_site_provider_broker import (
    publish_terminal_quota_record,
    read_terminal_quota_bytes,
)
from scripts.two_site_quota_resume_worker_two_file_reader import (
    read_and_bind_worker_two_result,
)
from scripts.two_site_quota_wait_admission import (
    WaitAdmissionError,
    admit_initial_quota_wait_locked,
)
from scripts.two_site_semantic_attempts import (
    extract_semantic_identity,
    read_existing_semantic_attempt,
)
from tests.test_two_site_provider_broker import (
    _make_quota_payload,
    _make_quota_response,
    _write_runner_status,
    broker_repo,
)
from tests.test_two_site_quota_resume_worker_two_fileproof import (
    _setup_materialized_worker_two_env,
)
from tests.test_two_site_quota_w2_common_root_receipt import (
    setup_two_site_quota_w2_common_root_reservation,
)


def _setup_w2_common_root_wait_scenario(
    root: Path,
    task: Path,
    original_scope: Any,
    original_environment: dict[str, str],
    frozen_clock_dt: datetime,
) -> dict[str, Any]:
    fixture = setup_two_site_quota_w2_common_root_reservation(
        root, task, original_scope, original_environment, publish=True
    )
    published_reservation = fixture["published"]
    assert published_reservation is not None

    head = fixture["head"]
    request = fixture["request"]
    run_prefix = request["run_prefix"]
    controller_run_id = fixture["controller_run_id"]
    accepted_facts = fixture["accepted_facts"]
    lock_path = fixture["lock_path"]
    runs_dir = fixture["runs_dir"]

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

    w1_status_path = (
        root / ".local" / "two-site-refactor" / "proposals" / meta["w1_id"] / "status.json"
    )
    r1_status_path = (
        root / ".local" / "two-site-refactor" / "proposals" / meta["r1_id"] / "status.json"
    )
    w1_bytes_snapshot = w1_status_path.read_bytes()
    r1_bytes_snapshot = r1_status_path.read_bytes()
    task_bytes_snapshot = task.read_bytes()
    task_sha_snapshot = hashlib.sha256(task_bytes_snapshot).hexdigest()

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

    reset_at_dt = (frozen_clock_dt - timedelta(seconds=60)).replace(microsecond=0)
    reset_at_utc = reset_at_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    updated_evidence = dict(payload["quota_evidence"])
    updated_evidence["reset_at_utc"] = reset_at_utc
    payload["quota_evidence"] = updated_evidence

    orig_w2_status_path, orig_w2_status_sha = _write_runner_status(
        root,
        orig_w2_id,
        role="worker",
        head=head,
        model=updated_evidence["model"],
        task_id=task_id,
        quota_evidence=updated_evidence,
        conversation_id=updated_evidence["conversation_id"],
    )

    payload["quota_attempt"]["status_sha256"] = orig_w2_status_sha
    assert payload["correction_count"] == 1
    assert payload["quota_attempt"]["role"] == "worker"
    assert payload["quota_attempt"]["run_id"] == orig_w2_id

    response = _make_quota_response(request, payload)
    published_receipt = publish_terminal_quota_record(root, request, response)
    raw_receipt_bytes = read_terminal_quota_bytes(root, request["request_id"])
    receipt_sha_snapshot = hashlib.sha256(raw_receipt_bytes).hexdigest()

    autopilot_dir = root / ".local" / "two-site-refactor" / "autopilot"
    epoch_dir = ensure_rescope_epoch_dir(autopilot_dir, accepted_facts)
    publish_rescope_intent(epoch_dir, 1)
    rescope_epoch = rescope_epoch_basename(accepted_facts)

    reservation_file = runs_dir / "broker-reservation.json"
    reservation_bytes_snapshot = reservation_file.read_bytes()

    return {
        "root": root,
        "task": task,
        "head": head,
        "request": request,
        "response": response,
        "controller_run_id": controller_run_id,
        "runs_dir": runs_dir,
        "lock_path": lock_path,
        "accepted_facts": accepted_facts,
        "published_reservation": published_reservation,
        "published_receipt": published_receipt,
        "identity": identity,
        "rescope_epoch": rescope_epoch,
        "orig_w2_id": orig_w2_id,
        "orig_w2_status_path": orig_w2_status_path,
        "orig_w2_status_sha": orig_w2_status_sha,
        "meta": meta,
        "w1_status_path": w1_status_path,
        "r1_status_path": r1_status_path,
        "frozen_clock_dt": frozen_clock_dt,
        "task_bytes_snapshot": task_bytes_snapshot,
        "task_sha_snapshot": task_sha_snapshot,
        "w1_bytes_snapshot": w1_bytes_snapshot,
        "r1_bytes_snapshot": r1_bytes_snapshot,
        "reservation_file": reservation_file,
        "reservation_bytes_snapshot": reservation_bytes_snapshot,
        "raw_receipt_bytes": raw_receipt_bytes,
        "receipt_sha_snapshot": receipt_sha_snapshot,
    }


def test_two_site_quota_w2_common_root_wait_positive(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    root, task, original_scope, original_environment = broker_repo
    frozen_clock_dt = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
    sc = _setup_w2_common_root_wait_scenario(
        root, task, original_scope, original_environment, frozen_clock_dt
    )

    with exclusive_supervisor_lock(sc["lock_path"]):
        wait_record = admit_initial_quota_wait_locked(
            root,
            sc["controller_run_id"],
            clock=lambda: frozen_clock_dt,
        )

    canonical_request_bytes = json.dumps(
        sc["request"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    expected_request_sha = hashlib.sha256(canonical_request_bytes).hexdigest()
    assert wait_record["request_sha256"] == expected_request_sha
    assert wait_record["request_sha256"] == sc["published_reservation"]["request_sha256"]
    assert wait_record["request_sha256"] == sc["published_receipt"]["request_sha256"]

    assert wait_record["terminal_receipt_sha256"] == sc["receipt_sha_snapshot"]

    canonical_response_bytes = json.dumps(
        sc["response"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    expected_response_sha = hashlib.sha256(canonical_response_bytes).hexdigest()
    assert wait_record["response_sha256"] == expected_response_sha
    assert wait_record["response_sha256"] == sc["published_receipt"]["response_sha256"]

    assert wait_record["quota_attempt"]["run_id"] == sc["orig_w2_id"]
    assert wait_record["quota_attempt"]["status_sha256"] == sc["orig_w2_status_sha"]
    assert (
        hashlib.sha256(sc["orig_w2_status_path"].read_bytes()).hexdigest()
        == sc["orig_w2_status_sha"]
    )

    assert wait_record["semantic_task_fingerprint"] == sc["identity"]["fingerprint"]
    assert wait_record["repository"] == sc["accepted_facts"]
    assert wait_record["rescope_epoch"] == sc["rescope_epoch"]
    assert wait_record["rescope_attempt"] == 1
    assert wait_record["schedule"]["reset_kind"] == "QUOTA_RESET_KNOWN"

    wait_file = sc["runs_dir"] / "quota-wait.json"
    assert wait_file.is_file()
    canonical_wait_bytes = json.dumps(
        wait_record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert wait_file.read_bytes() == canonical_wait_bytes
    assert stat.S_IMODE(wait_file.stat().st_mode) == 0o600

    assert task.read_bytes() == sc["task_bytes_snapshot"]
    assert hashlib.sha256(task.read_bytes()).hexdigest() == sc["task_sha_snapshot"]
    assert sc["w1_status_path"].read_bytes() == sc["w1_bytes_snapshot"]
    assert sc["r1_status_path"].read_bytes() == sc["r1_bytes_snapshot"]
    assert sc["reservation_file"].read_bytes() == sc["reservation_bytes_snapshot"]
    assert read_terminal_quota_bytes(root, sc["request"]["request_id"]) == sc["raw_receipt_bytes"]

    with exclusive_supervisor_lock(sc["lock_path"]):
        idempotent_read = admit_initial_quota_wait_locked(
            root,
            sc["controller_run_id"],
            clock=lambda: frozen_clock_dt,
        )
    assert idempotent_read == wait_record


def test_two_site_quota_w2_common_root_wait_negative_w2_status_tamper(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    root, task, original_scope, original_environment = broker_repo
    frozen_clock_dt = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
    sc = _setup_w2_common_root_wait_scenario(
        root, task, original_scope, original_environment, frozen_clock_dt
    )

    orig_w2_status_path = sc["orig_w2_status_path"]
    tampered = json.loads(orig_w2_status_path.read_text(encoding="utf-8"))
    tampered["drifted"] = True
    tampered["state"] = "CORRUPTED"
    orig_w2_status_path.write_text(
        json.dumps(tampered, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with exclusive_supervisor_lock(sc["lock_path"]):
        with pytest.raises(WaitAdmissionError):
            admit_initial_quota_wait_locked(
                root,
                sc["controller_run_id"],
                clock=lambda: frozen_clock_dt,
            )

    runs_dir = sc["runs_dir"]
    assert not (runs_dir / "quota-wait.json").exists()
    assert not (runs_dir / "quota-resume-claim.json").exists()

    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    assert not (proposals_dir / sc["orig_w2_id"] / "result.json").exists()
    assert not (proposals_dir / sc["orig_w2_id"] / "reviewer.json").exists()

    expected_runs = {
        f"{sc['request']['run_prefix']}-worker-0",
        f"{sc['request']['run_prefix']}-reviewer-0",
        sc["meta"]["w1_id"],
        sc["meta"]["r1_id"],
        sc["meta"]["w2_id"],
        sc["orig_w2_id"],
    }
    assert set(os.listdir(proposals_dir)) == expected_runs

    assert sc["reservation_file"].read_bytes() == sc["reservation_bytes_snapshot"]
    assert read_terminal_quota_bytes(root, sc["request"]["request_id"]) == sc["raw_receipt_bytes"]
    assert task.read_bytes() == sc["task_bytes_snapshot"]
    assert sc["w1_status_path"].read_bytes() == sc["w1_bytes_snapshot"]
    assert sc["r1_status_path"].read_bytes() == sc["r1_bytes_snapshot"]
