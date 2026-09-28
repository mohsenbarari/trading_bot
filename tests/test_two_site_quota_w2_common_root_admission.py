"""Hermetic same-root Broker reservation to initial quota wait, claim, and continuation admission proof."""
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

from scripts.two_site_assignment_scope import load_active_assignment_scope
from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_proposal_runner import load_task
from scripts.two_site_provider_broker import read_terminal_quota_bytes
from scripts.two_site_quota_continuation_admission import (
    ContinuationAdmissionError,
    validate_quota_continuation_request,
)
from scripts.two_site_quota_resume_claim_admission import (
    prepare_known_reset_resume_claim,
)
from scripts.two_site_quota_resume_claim_storage import (
    publish_known_reset_resume_claim,
    read_known_reset_resume_claim,
)
from scripts.two_site_quota_resume_request_record import (
    build_quota_resume_request,
)
from scripts.two_site_quota_wait_admission import admit_initial_quota_wait_locked
from scripts.two_site_quota_wait_sources import (
    collect_initial_wait_source_expected,
)
from scripts.two_site_quota_wait_storage import (
    read_initial_wait_record,
    read_initial_wait_record_bytes,
)
from scripts.two_site_semantic_attempts import read_existing_semantic_attempt
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_wait import (
    _setup_w2_common_root_wait_scenario,
)


# observational quota, no replay authority
def _setup_w2_admission_claim_scenario(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    frozen_clock_dt: datetime,
    origin: str = "REVIEWER",
) -> dict[str, Any]:
    root, task, original_scope, original_environment = broker_repo
    sc = _setup_w2_common_root_wait_scenario(
        root,
        task,
        original_scope,
        original_environment,
        frozen_clock_dt,
        origin=origin,
    )

    runs_dir = sc["runs_dir"]
    controller_run_id = sc["controller_run_id"]

    with exclusive_supervisor_lock(sc["lock_path"]):
        wait_record = admit_initial_quota_wait_locked(
            root,
            controller_run_id,
            clock=lambda: frozen_clock_dt,
        )
        expected_wait = collect_initial_wait_source_expected(
            root,
            controller_run_id,
            sc["rescope_epoch"],
            1,
        )
        raw_wait_bytes = read_initial_wait_record_bytes(root, controller_run_id)
        read_wait = read_initial_wait_record(
            root,
            controller_run_id,
            expected_wait,
        )
        assert read_wait == wait_record

        task_result = load_task(root, task)
        real_task = task_result[0] if isinstance(task_result, tuple) else task_result
        persisted_attempt = read_existing_semantic_attempt(
            root,
            root / ".local" / "two-site-refactor",
            real_task,
            sc["request"]["run_prefix"],
        )

        wait_file = runs_dir / "quota-wait.json"
        assert wait_file.is_file()
        actual_wait_bytes = wait_file.read_bytes()
        assert actual_wait_bytes == raw_wait_bytes
        orig_wait_sha256 = hashlib.sha256(actual_wait_bytes).hexdigest()

        created_dt = datetime.fromtimestamp(sc["request"]["created_at"], tz=timezone.utc)
        next_check_str = wait_record["schedule"]["next_check_at_utc"]
        next_check_dt = datetime.fromisoformat(next_check_str.replace("Z", "+00:00"))
        claimed_at_dt = (max(created_dt, next_check_dt) + timedelta(seconds=1)).replace(
            microsecond=0
        )
        claimed_at_utc = claimed_at_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        continuation_controller_run_id = "cycle-0001-20990101T000000Z-124"
        assert continuation_controller_run_id != controller_run_id

        resume_request_id = hashlib.sha256(
            f"resume-req-{sc['request']['request_id']}".encode("utf-8")
        ).hexdigest()[:32]
        assert resume_request_id != sc["request"]["request_id"]
        assert len(resume_request_id) == 32

        claim_prep = prepare_known_reset_resume_claim(
            wait_record=wait_record,
            expected_wait=expected_wait,
            semantic_attempt=persisted_attempt,
            expected_task_id=real_task["task_id"],
            original_wait_sha256=orig_wait_sha256,
            continuation_controller_run_id=continuation_controller_run_id,
            resume_request_id=resume_request_id,
            claimed_at_utc=claimed_at_utc,
        )
        claim = claim_prep["claim"]
        expected_claim = claim_prep["expected"]

        published_claim = publish_known_reset_resume_claim(
            root,
            controller_run_id,
            claim,
            expected_claim,
        )
        assert published_claim == claim

        read_claim = read_known_reset_resume_claim(
            root,
            controller_run_id,
            expected_claim,
        )
        assert read_claim == claim

        claim_file = runs_dir / "quota-resume-claim.json"
        assert claim_file.is_file()
        persisted_claim_bytes = claim_file.read_bytes()
        canonical_claim_bytes = json.dumps(
            claim,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        assert persisted_claim_bytes == canonical_claim_bytes
        claim_sha256 = hashlib.sha256(persisted_claim_bytes).hexdigest()

        now_epoch = claimed_at_dt.timestamp() + 1.0
        resume_request = build_quota_resume_request(
            claim=claim,
            expected_claim=expected_claim,
            expected_claim_sha256=claim_sha256,
            timeout=60,
            now_epoch=now_epoch,
        )

    expected_runs = {
        f"{sc['request']['run_prefix']}-worker-0",
        f"{sc['request']['run_prefix']}-reviewer-0",
        sc["meta"]["w1_id"],
        sc["meta"]["w2_id"],
        sc["orig_w2_id"],
    }
    r1_id = sc["meta"].get("r1_id")
    if isinstance(r1_id, str):
        expected_runs.add(r1_id)
    runs_dir_entries = set(os.listdir(runs_dir))

    return {
        "sc": sc,
        "root": root,
        "task": task,
        "real_task": real_task,
        "runs_dir": runs_dir,
        "runs_dir_entries": runs_dir_entries,
        "controller_run_id": controller_run_id,
        "continuation_controller_run_id": continuation_controller_run_id,
        "wait_record": wait_record,
        "wait_file": wait_file,
        "raw_wait_bytes": raw_wait_bytes,
        "orig_wait_sha256": orig_wait_sha256,
        "claim": claim,
        "expected_claim": expected_claim,
        "claim_file": claim_file,
        "persisted_claim_bytes": persisted_claim_bytes,
        "claim_sha256": claim_sha256,
        "claimed_at_dt": claimed_at_dt,
        "now_epoch": now_epoch,
        "resume_request": resume_request,
        "expected_runs": expected_runs,
    }


@pytest.mark.parametrize("origin", ["REVIEWER", "BOOTSTRAP", "TEST_FAILED"])
def test_two_site_quota_w2_common_root_admission_positive(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    origin: str,
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(
        broker_repo, frozen_clock_dt, origin=origin
    )

    root = ctx["root"]
    task = ctx["task"]
    sc = ctx["sc"]
    runs_dir = ctx["runs_dir"]
    controller_run_id = ctx["controller_run_id"]
    wait_file = ctx["wait_file"]
    raw_wait_bytes = ctx["raw_wait_bytes"]
    orig_wait_sha256 = ctx["orig_wait_sha256"]
    claim_file = ctx["claim_file"]
    persisted_claim_bytes = ctx["persisted_claim_bytes"]
    claim_sha256 = ctx["claim_sha256"]
    now_epoch = ctx["now_epoch"]
    resume_request = ctx["resume_request"]
    expected_runs = ctx["expected_runs"]

    admitted_request, admitted_task_path, admitted_scope = (
        validate_quota_continuation_request(
            root,
            resume_request,
            now_epoch=now_epoch,
        )
    )

    expected_scope = load_active_assignment_scope(root)
    assert admitted_request == resume_request
    assert admitted_task_path == task
    assert admitted_scope == expected_scope
    assert admitted_scope.assignment_id == sc["request"]["assignment_id"]
    assert admitted_scope.sha256 == sc["request"]["assignment_sha256"]

    assert stat.S_IMODE(wait_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(claim_file.stat().st_mode) == 0o600
    assert wait_file.read_bytes() == raw_wait_bytes
    assert hashlib.sha256(wait_file.read_bytes()).hexdigest() == orig_wait_sha256
    assert claim_file.read_bytes() == persisted_claim_bytes
    assert hashlib.sha256(claim_file.read_bytes()).hexdigest() == claim_sha256

    assert task.read_bytes() == sc["task_bytes_snapshot"]
    assert hashlib.sha256(task.read_bytes()).hexdigest() == sc["task_sha_snapshot"]
    assert sc["reservation_file"].read_bytes() == sc["reservation_bytes_snapshot"]
    assert (
        read_terminal_quota_bytes(root, sc["request"]["request_id"])
        == sc["raw_receipt_bytes"]
    )
    assert (
        hashlib.sha256(sc["raw_receipt_bytes"]).hexdigest()
        == sc["receipt_sha_snapshot"]
    )
    assert sc["w1_status_path"].read_bytes() == sc["w1_bytes_snapshot"]
    if origin == "REVIEWER":
        assert sc["r1_status_path"] is not None
        assert sc["r1_bytes_snapshot"] is not None
        assert sc["r1_status_path"].read_bytes() == sc["r1_bytes_snapshot"]
    elif origin == "BOOTSTRAP":
        assert sc["r1_status_path"] is None
        assert sc["r1_bytes_snapshot"] is None
        assert not (
            root
            / ".local"
            / "two-site-refactor"
            / "proposals"
            / f"{sc['request']['run_prefix']}-reviewer-1"
        ).exists()
        assert not (
            root
            / ".local"
            / "two-site-refactor"
            / "pipeline"
            / sc["request"]["run_prefix"]
            / "pre-review-tests-1.json"
        ).exists()
    elif origin == "TEST_FAILED":
        assert sc["r1_status_path"] is None
        assert sc["r1_bytes_snapshot"] is None
        assert not (
            root
            / ".local"
            / "two-site-refactor"
            / "proposals"
            / f"{sc['request']['run_prefix']}-reviewer-1"
        ).exists()
        receipt1_path = (
            root
            / ".local"
            / "two-site-refactor"
            / "pipeline"
            / sc["request"]["run_prefix"]
            / "pre-review-tests-1.json"
        )
        assert receipt1_path.is_file()
        receipt1_bytes = receipt1_path.read_bytes()
        receipt1_data = json.loads(receipt1_bytes)
        assert receipt1_data["state"] == "FAILED"
        assert receipt1_data["returncode"] != 0
        assert (
            hashlib.sha256(receipt1_bytes).hexdigest()
            == json.loads(sc["response"]["stdout"])["pre_review_test_sha256"]
        )
    else:
        raise AssertionError(f"unexpected origin: {origin!r}")
    assert (
        hashlib.sha256(sc["orig_w2_status_path"].read_bytes()).hexdigest()
        == sc["orig_w2_status_sha"]
    )

    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    assert set(os.listdir(proposals_dir)) == expected_runs
    assert not (proposals_dir / sc["orig_w2_id"] / "result.json").exists()
    assert not (proposals_dir / sc["orig_w2_id"] / "reviewer.json").exists()

    assert set(os.listdir(runs_dir)) == ctx["runs_dir_entries"]
    runs_parent = runs_dir.parent
    assert set(os.listdir(runs_parent)) == {controller_run_id}


@pytest.mark.parametrize("origin", ["REVIEWER", "BOOTSTRAP", "TEST_FAILED"])
def test_two_site_quota_w2_common_root_admission_negative_tampered_claim_sha(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    origin: str,
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(
        broker_repo, frozen_clock_dt, origin=origin
    )

    root = ctx["root"]
    task = ctx["task"]
    sc = ctx["sc"]
    runs_dir = ctx["runs_dir"]
    controller_run_id = ctx["controller_run_id"]
    wait_file = ctx["wait_file"]
    raw_wait_bytes = ctx["raw_wait_bytes"]
    orig_wait_sha256 = ctx["orig_wait_sha256"]
    claim_file = ctx["claim_file"]
    persisted_claim_bytes = ctx["persisted_claim_bytes"]
    claim_sha256 = ctx["claim_sha256"]
    now_epoch = ctx["now_epoch"]
    resume_request = ctx["resume_request"]
    expected_runs = ctx["expected_runs"]

    bad_claim_sha = "0" * 64 if claim_sha256 != "0" * 64 else "1" * 64
    tampered_resume_request = dict(resume_request)
    tampered_resume_request["claim_sha256"] = bad_claim_sha

    with pytest.raises(ContinuationAdmissionError) as exc_info:
        validate_quota_continuation_request(
            root,
            tampered_resume_request,
            now_epoch=now_epoch,
        )
    assert "claim SHA mismatch" in str(exc_info.value)

    assert stat.S_IMODE(wait_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(claim_file.stat().st_mode) == 0o600
    assert wait_file.read_bytes() == raw_wait_bytes
    assert hashlib.sha256(wait_file.read_bytes()).hexdigest() == orig_wait_sha256
    assert claim_file.read_bytes() == persisted_claim_bytes
    assert hashlib.sha256(claim_file.read_bytes()).hexdigest() == claim_sha256

    assert task.read_bytes() == sc["task_bytes_snapshot"]
    assert hashlib.sha256(task.read_bytes()).hexdigest() == sc["task_sha_snapshot"]
    assert sc["reservation_file"].read_bytes() == sc["reservation_bytes_snapshot"]
    assert (
        read_terminal_quota_bytes(root, sc["request"]["request_id"])
        == sc["raw_receipt_bytes"]
    )
    assert (
        hashlib.sha256(sc["raw_receipt_bytes"]).hexdigest()
        == sc["receipt_sha_snapshot"]
    )
    assert sc["w1_status_path"].read_bytes() == sc["w1_bytes_snapshot"]
    if origin == "REVIEWER":
        assert sc["r1_status_path"] is not None
        assert sc["r1_bytes_snapshot"] is not None
        assert sc["r1_status_path"].read_bytes() == sc["r1_bytes_snapshot"]
    elif origin == "BOOTSTRAP":
        assert sc["r1_status_path"] is None
        assert sc["r1_bytes_snapshot"] is None
        assert not (
            root
            / ".local"
            / "two-site-refactor"
            / "proposals"
            / f"{sc['request']['run_prefix']}-reviewer-1"
        ).exists()
        assert not (
            root
            / ".local"
            / "two-site-refactor"
            / "pipeline"
            / sc["request"]["run_prefix"]
            / "pre-review-tests-1.json"
        ).exists()
    elif origin == "TEST_FAILED":
        assert sc["r1_status_path"] is None
        assert sc["r1_bytes_snapshot"] is None
        assert not (
            root
            / ".local"
            / "two-site-refactor"
            / "proposals"
            / f"{sc['request']['run_prefix']}-reviewer-1"
        ).exists()
        receipt1_path = (
            root
            / ".local"
            / "two-site-refactor"
            / "pipeline"
            / sc["request"]["run_prefix"]
            / "pre-review-tests-1.json"
        )
        assert receipt1_path.is_file()
        receipt1_bytes = receipt1_path.read_bytes()
        receipt1_data = json.loads(receipt1_bytes)
        assert receipt1_data["state"] == "FAILED"
        assert receipt1_data["returncode"] != 0
        assert (
            hashlib.sha256(receipt1_bytes).hexdigest()
            == json.loads(sc["response"]["stdout"])["pre_review_test_sha256"]
        )
    else:
        raise AssertionError(f"unexpected origin: {origin!r}")
    assert (
        hashlib.sha256(sc["orig_w2_status_path"].read_bytes()).hexdigest()
        == sc["orig_w2_status_sha"]
    )

    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    assert set(os.listdir(proposals_dir)) == expected_runs
    assert not (proposals_dir / sc["orig_w2_id"] / "result.json").exists()
    assert not (proposals_dir / sc["orig_w2_id"] / "reviewer.json").exists()

    assert set(os.listdir(runs_dir)) == ctx["runs_dir_entries"]
    runs_parent = runs_dir.parent
    assert set(os.listdir(runs_parent)) == {controller_run_id}
