"""observational quota, no replay authority: hermetic fileproof join test.

Explicit disclosures:
1. scripts.two_site_quota_wait_intent.validate_repository is patched to return
   explicit accepted repository facts because the tiny temp Git repository lacks
   the full Autopilot control-file tree.
2. scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected
   is patched to return exact expected_wait because the initial wait collector is
   the explicit external boundary for this fileproof join.
3. Original quota terminal receipt and response status hashes remain synthetic;
   they are not real receipts.

This test does not claim full real collector, terminal receipt, W2, Provider
execution, live hook, replay authority or C5d completion.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time
from typing import Any
from unittest import mock

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_quota_continuation_admission import (
    ContinuationAdmissionError,
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
from scripts.two_site_quota_wait_record import validate_initial_wait_record
from scripts.two_site_quota_wait_schedule import initial_wait_schedule
from scripts.two_site_quota_wait_storage import (
    publish_initial_wait_record_bytes,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_provider_broker_cycle_reservation_read import (
    _setup_cycle_root,
    _write_startup_json,
)
from tests.test_two_site_quota_continuation_common_intent_fileproof import (
    _setup_intent_marker,
)
from tests.test_two_site_quota_join_semantic_fixture import (
    setup_two_site_quota_join_semantic_fixture,
)
from tests.test_two_site_quota_resume_claim_admission import (
    _build_valid_wait_fixtures,
)

_EXPECTED_WAIT_KEYS = (
    "controller_run_id",
    "request_id",
    "reservation",
    "request_sha256",
    "terminal_receipt_sha256",
    "response_sha256",
    "semantic_task_fingerprint",
    "rescope_epoch",
    "rescope_attempt",
    "quota_attempt",
    "quota_evidence",
    "repository",
)


def _setup_joined_wait_claim_scenario(
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

    repo_facts, validated_repo, epoch_dir, marker_path, intent_epoch = (
        _setup_intent_marker(root, actual_scope)
    )

    record, _ = _build_valid_wait_fixtures(known_reset=True)
    record["controller_run_id"] = original_ctrl_id
    record["request_id"] = request["request_id"]
    record["reservation"] = {
        "controller_run_id": request["controller_run_id"],
        "run_prefix": request["run_prefix"],
        "task_path": request["task_path"],
        "task_sha256": request["task_sha256"],
        "source_head": request["source_head"],
        "assignment_id": request["assignment_id"],
        "assignment_sha256": request["assignment_sha256"],
    }
    record["repository"] = copy.deepcopy(validated_repo)
    record["semantic_task_fingerprint"] = fixture["identity"]["fingerprint"]
    record["rescope_epoch"] = intent_epoch
    record["rescope_attempt"] = 1
    record["quota_attempt"]["run_id"] = f"{request['run_prefix']}-worker-0"

    # Replace quota evidence reset_at_utc with now-100 seconds and recompute schedule
    created_at_dt = (now_utc - timedelta(seconds=120)).replace(microsecond=0)
    reset_at_dt = (now_utc - timedelta(seconds=100)).replace(microsecond=0)
    created_at_utc = created_at_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    reset_at_utc = reset_at_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    record["quota_evidence"]["reset_at_utc"] = reset_at_utc
    schedule = initial_wait_schedule(record["quota_evidence"], created_at_utc)
    record["schedule"] = schedule

    expected_wait = {k: copy.deepcopy(record[k]) for k in _EXPECTED_WAIT_KEYS}
    validated_wait = validate_initial_wait_record(record, expected_wait)
    assert validated_wait == record

    _, runs_dir = _setup_cycle_root(str(root), original_ctrl_id)
    _write_startup_json(
        runs_dir,
        cycle_id=original_ctrl_id,
        source_head=request["source_head"],
        assignment_sha256=actual_scope.sha256,
    )

    raw_wait_text = json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    raw_wait_bytes = raw_wait_text.encode("utf-8")
    orig_wait_sha256 = hashlib.sha256(raw_wait_bytes).hexdigest()

    claimed_at_dt = (now_utc - timedelta(seconds=60)).replace(microsecond=0)
    claimed_at_utc = claimed_at_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    resume_request_id = hashlib.sha256(
        f"resume-req-{original_ctrl_id}".encode("utf-8")
    ).hexdigest()[:32]

    claim_prep = prepare_known_reset_resume_claim(
        wait_record=record,
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

    autopilot_dir = root / ".local" / "two-site-refactor" / "autopilot"
    with exclusive_supervisor_lock(autopilot_dir / "supervisor.lock"):
        pub_wait = publish_initial_wait_record_bytes(
            root,
            original_ctrl_id,
            raw_wait_bytes,
        )
        pub_claim = publish_known_reset_resume_claim(
            root,
            original_ctrl_id,
            claim,
            expected_claim,
        )

    assert pub_wait == raw_wait_bytes
    assert pub_claim == claim

    wait_file = runs_dir / "quota-wait.json"
    claim_file = runs_dir / "quota-resume-claim.json"
    assert wait_file.is_file()
    assert claim_file.is_file()
    assert wait_file.read_bytes() == raw_wait_bytes
    assert claim_file.read_bytes() == claim_bytes
    assert stat.S_IMODE(wait_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(claim_file.stat().st_mode) == 0o600

    return {
        "root": root,
        "task": task,
        "head": head,
        "actual_scope": actual_scope,
        "request": request,
        "record": record,
        "expected_wait": expected_wait,
        "raw_wait_bytes": raw_wait_bytes,
        "claim": claim,
        "expected_claim": expected_claim,
        "claim_bytes": claim_bytes,
        "resume_request": resume_request,
        "repo_facts": repo_facts,
        "validated_repo": validated_repo,
        "marker_path": marker_path,
        "intent_epoch": intent_epoch,
        "original_ctrl_id": original_ctrl_id,
        "continuation_ctrl_id": continuation_ctrl_id,
        "runs_dir": runs_dir,
        "wait_file": wait_file,
        "claim_file": claim_file,
    }


def test_quota_common_wait_claim_join_fileproof_positive(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    """Positive hermetic join of real canonical Broker/Assignment/task/semantic fixture.

    Scope: observational quota, no replay authority.
    Validates that full continuation admission succeeds through unpatched
    validate_quota_continuation_request, patching only explicit Autopilot repo facts
    and initial expected-wait collector.
    """
    root, task, original_scope, original_environment = broker_repo
    sc = _setup_joined_wait_claim_scenario(
        root, task, original_scope, original_environment
    )

    now_epoch = time.time()
    with (
        mock.patch(
            "scripts.two_site_quota_wait_intent.validate_repository",
            side_effect=lambda *args, **kwargs: copy.deepcopy(sc["repo_facts"]),
        ) as mock_validate_repo,
        mock.patch(
            "scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected",
            side_effect=lambda *args, **kwargs: copy.deepcopy(sc["expected_wait"]),
        ) as mock_collect,
    ):
        admitted_request, admitted_task_path, admitted_scope = (
            validate_quota_continuation_request(
                sc["root"],
                sc["resume_request"],
                scope_loader=lambda _: sc["actual_scope"],
                now_epoch=now_epoch,
            )
        )

    assert admitted_request == sc["resume_request"]
    assert admitted_task_path == sc["task"]
    assert admitted_scope == sc["actual_scope"]

    assert mock_collect.call_count == 2
    mock_collect.assert_has_calls(
        [
            mock.call(
                sc["root"].resolve(),
                sc["original_ctrl_id"],
                sc["intent_epoch"],
                1,
            ),
            mock.call(
                sc["root"].resolve(),
                sc["original_ctrl_id"],
                sc["intent_epoch"],
                1,
            ),
        ]
    )
    assert mock_validate_repo.call_count >= 2

    git_status = subprocess.check_output(
        ["git", "-C", str(sc["root"]), "status", "--porcelain=v1"],
        text=True,
    ).strip()
    assert git_status == ""
    current_head = subprocess.check_output(
        ["git", "-C", str(sc["root"]), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    assert current_head == sc["head"]

    assert sc["wait_file"].read_bytes() == sc["raw_wait_bytes"]
    assert sc["claim_file"].read_bytes() == sc["claim_bytes"]
    assert stat.S_IMODE(sc["wait_file"].stat().st_mode) == 0o600
    assert stat.S_IMODE(sc["claim_file"].stat().st_mode) == 0o600
    assert (sc["marker_path"].stat().st_mode & 0o777) == 0o600


def test_quota_common_wait_claim_join_fileproof_negative_wait_mode_tamper(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    """Wait-mode 0644 tamper fails closed after common admission before task/claim acceptance.

    Scope: observational quota, no replay authority.
    Validates that 0644 permissions on quota-wait.json trigger ContinuationAdmissionError
    with a 0600 mode cause, without tracked Git mutation or Provider execution.
    """
    root, task, original_scope, original_environment = broker_repo
    sc = _setup_joined_wait_claim_scenario(
        root, task, original_scope, original_environment
    )

    wait_file = sc["wait_file"]
    claim_file = sc["claim_file"]
    raw_wait_bytes = sc["raw_wait_bytes"]
    claim_bytes = sc["claim_bytes"]
    head = sc["head"]

    os.chmod(wait_file, 0o644)
    try:
        now_epoch = time.time()
        with (
            mock.patch(
                "scripts.two_site_quota_wait_intent.validate_repository",
                side_effect=lambda *args, **kwargs: copy.deepcopy(sc["repo_facts"]),
            ),
            mock.patch(
                "scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected",
                side_effect=lambda *args, **kwargs: copy.deepcopy(sc["expected_wait"]),
            ) as mock_collect,
        ):
            with pytest.raises(ContinuationAdmissionError) as exc_info:
                validate_quota_continuation_request(
                    sc["root"],
                    sc["resume_request"],
                    scope_loader=lambda _: sc["actual_scope"],
                    now_epoch=now_epoch,
                )

        assert exc_info.value.__cause__ is not None
        assert "0600" in str(exc_info.value.__cause__)
        assert "failed to read initial wait record:" in str(exc_info.value)

        # Common request validation and initial intent collector succeeded (call 1),
        # but failure halted execution before task load or claim acceptance.
        assert mock_collect.call_count == 1
    finally:
        os.chmod(wait_file, 0o600)

    git_status = subprocess.check_output(
        ["git", "-C", str(sc["root"]), "status", "--porcelain=v1"],
        text=True,
    ).strip()
    assert git_status == ""
    current_head = subprocess.check_output(
        ["git", "-C", str(sc["root"]), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    assert current_head == head

    assert wait_file.read_bytes() == raw_wait_bytes
    assert claim_file.read_bytes() == claim_bytes
    assert stat.S_IMODE(wait_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(claim_file.stat().st_mode) == 0o600
