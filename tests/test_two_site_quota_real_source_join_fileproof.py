"""observational quota, no replay authority: hermetic real-source join fileproof.

Explicit disclosures:
1. Synthetic Provider outcome: the original quota Provider payload and response are
   synthetic test outcomes constructed via _make_quota_payload and _make_quota_response;
   they materialize an actual file-backed status witness and real Broker terminal
   quota receipt, but are not produced by live Provider execution.
2. Real unpatched Autopilot repository facts: repository facts are validated directly
   from the temporary repository populated with complete tracked CONTROL_PATHS and
   GEMINI_RUNTIME_REFERENCE_PATHS, with no mocks of validate_repository or
   validate_accepted_repository, and a real pending RESCOPE intent is published.
3. Open W2, candidate sandbox, live hooks, and replay gates: original Provider quota
   response remains synthetic; candidate sandbox, W2 outer join, Broker/Supervisor live
   hook, STOP/race/crash, replay authority and full C5d completion remain OPEN and unproven.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time
from typing import Any

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_codex_autopilot import (
    PROTECTED_REFS,
    ensure_rescope_epoch_dir,
    exclusive_supervisor_lock,
    publish_rescope_intent,
    rescope_epoch_basename,
    validate_accepted_repository,
    validate_repository,
)
from scripts.two_site_provider_broker import (
    publish_cycle_reservation,
    publish_terminal_quota_record,
    read_terminal_quota_bytes,
    read_terminal_quota_record,
)
from scripts.two_site_quota_continuation_admission import (
    validate_quota_continuation_request,
)
from scripts.two_site_quota_resume_claim_admission import (
    prepare_known_reset_resume_claim,
)
from scripts.two_site_quota_resume_claim_storage import (
    publish_known_reset_resume_claim,
)
from scripts.two_site_quota_resume_request_record import (
    build_quota_resume_request,
)
from scripts.two_site_quota_wait_admission import (
    WaitAdmissionError,
    admit_initial_quota_wait_locked,
)
from scripts.two_site_quota_wait_intent import RESCOPE_INTENT_FILENAMES
from scripts.two_site_quota_wait_sources import (
    collect_initial_wait_source_expected,
)
from tests.test_two_site_provider_broker import (
    _make_quota_payload,
    _make_quota_response,
    _write_runner_status,
    broker_repo,
)
from tests.test_two_site_provider_broker_cycle_reservation_read import (
    _setup_cycle_root,
    _write_startup_json,
)
from tests.test_two_site_quota_full_tree_repository_facts import (
    _populate_full_tree_repo,
)
from tests.test_two_site_quota_join_semantic_fixture import (
    setup_two_site_quota_join_semantic_fixture,
)


def _setup_scenario_through_receipt(
    root: Path,
    task: Path,
    original_scope: Any,
    original_environment: dict[str, str],
) -> dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    year = now_utc.year
    stamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    original_ctrl_id = f"cycle-{year}-{stamp}-101"
    continuation_ctrl_id = f"cycle-{year}-{stamp}-202"

    updated_env = dict(original_environment)
    updated_env["TWO_SITE_AUTOPILOT_RUN_ID"] = original_ctrl_id

    _populate_full_tree_repo(root)

    fixture = setup_two_site_quota_join_semantic_fixture(
        root,
        task,
        original_scope,
        updated_env,
    )
    actual_scope = fixture["actual_scope"]
    request = fixture["request"]
    loaded_task = fixture["loaded_task"]
    persisted_attempt = fixture["persisted_attempt"]
    head = fixture["head"]
    identity = fixture["identity"]

    for ref in PROTECTED_REFS:
        subprocess.run(["git", "-C", str(root), "update-ref", ref, head], check=True)

    repo_facts = validate_repository(root)
    validated_repo = validate_accepted_repository(repo_facts)

    autopilot_dir = root / ".local" / "two-site-refactor" / "autopilot"
    autopilot_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(autopilot_dir, 0o700)

    epoch_dir = ensure_rescope_epoch_dir(autopilot_dir, validated_repo)
    publish_rescope_intent(epoch_dir, 1)

    marker_filename = RESCOPE_INTENT_FILENAMES[1]
    marker_path = epoch_dir / marker_filename
    assert marker_path.is_file()
    assert (marker_path.stat().st_mode & 0o777) == 0o600

    intent_epoch = rescope_epoch_basename(validated_repo)

    _, runs_dir = _setup_cycle_root(str(root), original_ctrl_id)
    startup_path = _write_startup_json(
        runs_dir,
        cycle_id=original_ctrl_id,
        source_head=request["source_head"],
        assignment_sha256=actual_scope.sha256,
    )

    autopilot_dir = root / ".local" / "two-site-refactor" / "autopilot"
    with exclusive_supervisor_lock(autopilot_dir / "supervisor.lock"):
        publish_cycle_reservation(root, request)

    payload = _make_quota_payload(
        root,
        loaded_task["task_id"],
        request["source_head"],
        run_prefix=request["run_prefix"],
    )

    reset_at_dt = (now_utc - timedelta(seconds=100)).replace(microsecond=0)
    reset_at_utc = reset_at_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    updated_evidence = dict(payload["quota_evidence"])
    updated_evidence["reset_at_utc"] = reset_at_utc
    payload["quota_evidence"] = updated_evidence

    status_path, status_sha = _write_runner_status(
        root,
        payload["quota_attempt"]["run_id"],
        role="worker",
        head=request["source_head"],
        model=payload["quota_evidence"]["model"],
        task_id=loaded_task["task_id"],
        quota_evidence=updated_evidence,
        conversation_id=updated_evidence["conversation_id"],
    )

    payload["quota_attempt"]["status_sha256"] = status_sha
    payload["semantic_task_fingerprint"] = identity["fingerprint"]

    response = _make_quota_response(request, payload)

    published_receipt = publish_terminal_quota_record(root, request, response)
    read_receipt = read_terminal_quota_record(root, request["request_id"])
    receipt_bytes = read_terminal_quota_bytes(root, request["request_id"])
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()

    assert read_receipt == published_receipt
    assert read_receipt["request"] == request
    assert read_receipt["response"] == response
    assert read_receipt["request"]["request_id"] == request["request_id"]
    assert read_receipt["request_sha256"] == published_receipt["request_sha256"]
    assert read_receipt["response_sha256"] == published_receipt["response_sha256"]
    assert json.loads(receipt_bytes.decode("utf-8")) == read_receipt

    return {
        "root": root,
        "task": task,
        "head": head,
        "actual_scope": actual_scope,
        "request": request,
        "response": response,
        "loaded_task": loaded_task,
        "persisted_attempt": persisted_attempt,
        "identity": identity,
        "repo_facts": repo_facts,
        "validated_repo": validated_repo,
        "epoch_dir": epoch_dir,
        "marker_path": marker_path,
        "intent_epoch": intent_epoch,
        "original_ctrl_id": original_ctrl_id,
        "continuation_ctrl_id": continuation_ctrl_id,
        "runs_dir": runs_dir,
        "startup_path": startup_path,
        "autopilot_dir": autopilot_dir,
        "status_path": status_path,
        "published_receipt": published_receipt,
        "receipt_sha": receipt_sha,
        "now_utc": now_utc,
    }


def test_quota_real_source_join_fileproof_positive(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    """Positive hermetic join of real canonical Broker/Assignment/task/wait/claim chain.

    Scope: observational quota, no replay authority.
    Validates that initial wait admission, receipt collector, wait record publication,
    known-reset claim preparation, claim publication, and continuation admission all succeed
    with real unpatched full-tree Autopilot repository facts and real pending intent.
    Original Provider quota response remains synthetic; candidate sandbox, W2 outer join,
    Broker/Supervisor live hook, STOP/race/crash, replay authority and full C5d completion
    remain OPEN and unproven.
    """
    root, task, original_scope, original_environment = broker_repo
    sc = _setup_scenario_through_receipt(
        root, task, original_scope, original_environment
    )

    now_utc = sc["now_utc"]
    autopilot_dir = sc["autopilot_dir"]
    original_ctrl_id = sc["original_ctrl_id"]
    continuation_ctrl_id = sc["continuation_ctrl_id"]
    published_receipt = sc["published_receipt"]
    receipt_sha = sc["receipt_sha"]
    response = sc["response"]
    identity = sc["identity"]
    intent_epoch = sc["intent_epoch"]
    runs_dir = sc["runs_dir"]
    persisted_attempt = sc["persisted_attempt"]
    loaded_task = sc["loaded_task"]
    actual_scope = sc["actual_scope"]
    request = sc["request"]
    marker_path = sc["marker_path"]
    head = sc["head"]

    clock_dt = now_utc - timedelta(seconds=120)
    with exclusive_supervisor_lock(autopilot_dir / "supervisor.lock"):
        wait_record = admit_initial_quota_wait_locked(
            root,
            original_ctrl_id,
            clock=lambda: clock_dt,
        )

        assert wait_record["request_sha256"] == published_receipt["request_sha256"]
        assert wait_record["response_sha256"] == published_receipt["response_sha256"]
        assert wait_record["terminal_receipt_sha256"] == receipt_sha
        raw_response_bytes = json.dumps(
            response,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        expected_response_sha = hashlib.sha256(raw_response_bytes).hexdigest()
        assert wait_record["response_sha256"] == expected_response_sha
        assert wait_record["semantic_task_fingerprint"] == identity["fingerprint"]

        next_check_str = wait_record["schedule"]["next_check_at_utc"]
        next_check_dt = datetime.fromisoformat(next_check_str.replace("Z", "+00:00"))
        expected_near_due = now_utc - timedelta(seconds=70)
        assert abs((next_check_dt - expected_near_due).total_seconds()) <= 5
        assert wait_record["schedule"]["reset_kind"] == "QUOTA_RESET_KNOWN"

        expected_wait = collect_initial_wait_source_expected(
            root,
            original_ctrl_id,
            intent_epoch,
            1,
        )

        wait_file = runs_dir / "quota-wait.json"
        assert wait_file.is_file()
        raw_wait_bytes = wait_file.read_bytes()
        orig_wait_sha256 = hashlib.sha256(raw_wait_bytes).hexdigest()

        claimed_at_dt = (now_utc - timedelta(seconds=60)).replace(microsecond=0)
        claimed_at_utc = claimed_at_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        resume_request_id = hashlib.sha256(
            f"resume-req-{original_ctrl_id}".encode("utf-8")
        ).hexdigest()[:32]

        claim_prep = prepare_known_reset_resume_claim(
            wait_record=wait_record,
            expected_wait=expected_wait,
            semantic_attempt=persisted_attempt,
            expected_task_id=loaded_task["task_id"],
            original_wait_sha256=orig_wait_sha256,
            continuation_controller_run_id=continuation_ctrl_id,
            resume_request_id=resume_request_id,
            claimed_at_utc=claimed_at_utc,
        )
        claim = claim_prep["claim"]
        expected_claim = claim_prep["expected"]

        pub_claim = publish_known_reset_resume_claim(
            root,
            original_ctrl_id,
            claim,
            expected_claim,
        )
        assert pub_claim == claim

        claim_text = json.dumps(
            claim,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        claim_bytes = claim_text.encode("utf-8")
        claim_sha256 = hashlib.sha256(claim_bytes).hexdigest()

        now_epoch = time.time()
        resume_request = build_quota_resume_request(
            claim=claim,
            expected_claim=expected_claim,
            expected_claim_sha256=claim_sha256,
            timeout=60,
            now_epoch=now_epoch,
        )

        admitted_request, admitted_task_path, admitted_scope = (
            validate_quota_continuation_request(
                root,
                resume_request,
                scope_loader=lambda _: actual_scope,
                now_epoch=now_epoch,
            )
        )

    assert admitted_request == resume_request
    assert admitted_task_path == task
    assert admitted_scope == actual_scope

    claim_file = runs_dir / "quota-resume-claim.json"
    assert claim_file.is_file()
    assert claim_file.read_bytes() == claim_bytes
    assert stat.S_IMODE(wait_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(claim_file.stat().st_mode) == 0o600
    assert (marker_path.stat().st_mode & 0o777) == 0o600

    matching_receipts = list(root.glob(f"**/{request['request_id']}.json"))
    assert len(matching_receipts) == 1
    receipt_file = matching_receipts[0]
    assert receipt_file.is_file()
    assert stat.S_IMODE(receipt_file.stat().st_mode) == 0o600

    git_status = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain=v1"],
        text=True,
    ).strip()
    assert git_status == ""
    current_head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    assert current_head == head


def test_quota_real_source_join_fileproof_negative_status_witness_tamper(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    """Post-receipt status-witness tamper fails closed in real collector.

    Scope: observational quota, no replay authority.
    Discloses that the original quota Provider outcome is synthetic and uses
    real unpatched full-tree Autopilot repository facts with real pending intent.
    Verifies that tampering the status witness after receipt publication causes
    admit_initial_quota_wait_locked to fail closed with WaitAdmissionError caused
    by collector/receipt witness rejection before wait or claim records are written,
    with no Provider execution and unchanged tracked Git.
    Candidate sandbox, W2 outer join, Broker/Supervisor live hook, STOP/race/crash,
    replay authority and full C5d completion remain OPEN and unproven.
    """
    root, task, original_scope, original_environment = broker_repo
    sc = _setup_scenario_through_receipt(
        root, task, original_scope, original_environment
    )

    status_path = sc["status_path"]
    original_ctrl_id = sc["original_ctrl_id"]
    autopilot_dir = sc["autopilot_dir"]
    runs_dir = sc["runs_dir"]
    head = sc["head"]
    now_utc = sc["now_utc"]

    status_path.write_bytes(b'{"drifted": true, "tampered": true}')

    clock_dt = now_utc - timedelta(seconds=120)
    with exclusive_supervisor_lock(autopilot_dir / "supervisor.lock"):
        with pytest.raises(WaitAdmissionError) as exc_info:
            admit_initial_quota_wait_locked(
                root,
                original_ctrl_id,
                clock=lambda: clock_dt,
            )

    assert exc_info.value.__cause__ is not None

    wait_file = runs_dir / "quota-wait.json"
    claim_file = runs_dir / "quota-resume-claim.json"
    assert not wait_file.exists()
    assert not claim_file.exists()

    git_status = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain=v1"],
        text=True,
    ).strip()
    assert git_status == ""
    current_head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    assert current_head == head
