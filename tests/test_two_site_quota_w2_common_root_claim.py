"""Hermetic same-root Broker reservation to initial quota wait and resume claim proof (observational quota, no replay authority)."""
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

from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_proposal_runner import load_task
from scripts.two_site_provider_broker import read_terminal_quota_bytes
from scripts.two_site_quota_resume_claim_admission import (
    prepare_known_reset_resume_claim,
)
from scripts.two_site_quota_resume_claim_storage import (
    ClaimStorageError,
    publish_known_reset_resume_claim,
    read_known_reset_resume_claim,
)
from scripts.two_site_quota_wait_admission import admit_initial_quota_wait_locked
from scripts.two_site_quota_wait_sources import (
    collect_initial_wait_source_expected,
)
from scripts.two_site_quota_wait_storage import (
    WaitStorageError,
    read_initial_wait_record,
)
from scripts.two_site_semantic_attempts import read_existing_semantic_attempt
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_wait import (
    _setup_w2_common_root_wait_scenario,
)


@pytest.mark.parametrize("origin", ["REVIEWER", "BOOTSTRAP", "TEST_FAILED"])
def test_two_site_quota_w2_common_root_claim_positive(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    origin: str,
) -> None:
    root, task, original_scope, original_environment = broker_repo
    frozen_clock_dt = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
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
        orig_wait_sha256 = hashlib.sha256(actual_wait_bytes).hexdigest()

        created_dt = datetime.fromtimestamp(sc["request"]["created_at"], tz=timezone.utc)
        next_check_str = wait_record["schedule"]["next_check_at_utc"]
        next_check_dt = datetime.fromisoformat(next_check_str.replace("Z", "+00:00"))
        claimed_at_dt = (max(next_check_dt, created_dt) + timedelta(seconds=15)).replace(
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
        canonical_claim_bytes = json.dumps(
            claim,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        assert claim_file.read_bytes() == canonical_claim_bytes
        assert stat.S_IMODE(claim_file.stat().st_mode) == 0o600

        with pytest.raises(ClaimStorageError) as exc_info:
            publish_known_reset_resume_claim(
                root,
                controller_run_id,
                claim,
                expected_claim,
            )
        assert "already exists" in str(exc_info.value)
        assert claim_file.read_bytes() == canonical_claim_bytes

        assert claim["original_controller_run_id"] == controller_run_id
        assert claim["continuation_controller_run_id"] == continuation_controller_run_id
        assert claim["original_request_id"] == sc["request"]["request_id"]
        assert claim["resume_request_id"] == resume_request_id
        assert claim["original_wait_sha256"] == orig_wait_sha256
        assert claim["semantic_task_fingerprint"] == sc["identity"]["fingerprint"]
        assert claim["task_id"] == real_task["task_id"]
        assert claim["task_path"] == sc["request"]["task_path"]
        assert claim["task_sha256"] == sc["request"]["task_sha256"]
        assert claim["source_head"] == sc["head"]
        assert claim["assignment_id"] == sc["request"]["assignment_id"]
        assert claim["assignment_sha256"] == sc["request"]["assignment_sha256"]
        assert claim["original_run_prefix"] == sc["request"]["run_prefix"]
        assert claim["resume_run_prefix"] == f"qresume-{sc['request']['request_id']}"
        assert claim["role"] == "worker"
        assert claim["reset_kind"] == "QUOTA_RESET_KNOWN"
        assert claim["next_check_at_utc"] == wait_record["schedule"]["next_check_at_utc"]
        assert claim["claimed_at_utc"] == claimed_at_utc
        assert claim["remaining_resume_budget_before"] == 1
        assert claim["remaining_resume_budget_after"] == 0

        assert wait_file.read_bytes() == actual_wait_bytes
        assert sc["reservation_file"].read_bytes() == sc["reservation_bytes_snapshot"]
        assert (
            read_terminal_quota_bytes(root, sc["request"]["request_id"])
            == sc["raw_receipt_bytes"]
        )
        assert sc["w1_status_path"].read_bytes() == sc["w1_bytes_snapshot"]
        if origin == "BOOTSTRAP":
            assert sc["r1_status_path"] is None
            assert sc["r1_bytes_snapshot"] is None
            assert not (
                root
                / ".local"
                / "two-site-refactor"
                / "pipeline"
                / sc["request"]["run_prefix"]
                / "pre-review-tests-1.json"
            ).exists()
            assert not (
                root
                / ".local"
                / "two-site-refactor"
                / "proposals"
                / f"{sc['request']['run_prefix']}-reviewer-1"
            ).exists()
            assert sc["meta"].get("r1_id") is None
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
            assert sc["meta"].get("r1_id") is None
            receipt1_path = (
                root
                / ".local"
                / "two-site-refactor"
                / "pipeline"
                / sc["request"]["run_prefix"]
                / "pre-review-tests-1.json"
            )
            assert receipt1_path.is_file()
            receipt1_data = json.loads(receipt1_path.read_text(encoding="utf-8"))
            assert receipt1_data["state"] == "FAILED"
            assert receipt1_data["returncode"] != 0
            assert (
                hashlib.sha256(receipt1_path.read_bytes()).hexdigest()
                == json.loads(sc["response"]["stdout"])["pre_review_test_sha256"]
            )
        else:
            assert sc["r1_status_path"] is not None
            assert sc["r1_status_path"].read_bytes() == sc["r1_bytes_snapshot"]
        assert (
            hashlib.sha256(sc["orig_w2_status_path"].read_bytes()).hexdigest()
            == sc["orig_w2_status_sha"]
        )
        assert task.read_bytes() == sc["task_bytes_snapshot"]


@pytest.mark.parametrize("origin", ["REVIEWER", "BOOTSTRAP", "TEST_FAILED"])
def test_two_site_quota_w2_common_root_claim_negative_tampered_wait(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    origin: str,
) -> None:
    root, task, original_scope, original_environment = broker_repo
    frozen_clock_dt = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
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
        admit_initial_quota_wait_locked(
            root,
            controller_run_id,
            clock=lambda: frozen_clock_dt,
        )
        wait_file = runs_dir / "quota-wait.json"
        assert wait_file.is_file()
        assert stat.S_IMODE(wait_file.stat().st_mode) == 0o600

        wait_payload = json.loads(wait_file.read_text(encoding="utf-8"))
        wait_payload["state"] = "CORRUPTED_STATE"
        tampered_bytes = json.dumps(
            wait_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        wait_file.write_bytes(tampered_bytes)
        os.chmod(wait_file, 0o600)
        assert stat.S_IMODE(wait_file.stat().st_mode) == 0o600

        expected_wait = collect_initial_wait_source_expected(
            root,
            controller_run_id,
            sc["rescope_epoch"],
            1,
        )
        with pytest.raises(WaitStorageError) as exc_info:
            read_initial_wait_record(
                root,
                controller_run_id,
                expected_wait,
            )
        assert "initial wait record validation failed" in str(exc_info.value)

        claim_file = runs_dir / "quota-resume-claim.json"
        assert not claim_file.exists()

        assert task.read_bytes() == sc["task_bytes_snapshot"]
        assert sc["reservation_file"].read_bytes() == sc["reservation_bytes_snapshot"]
        assert (
            read_terminal_quota_bytes(root, sc["request"]["request_id"])
            == sc["raw_receipt_bytes"]
        )
        assert sc["w1_status_path"].read_bytes() == sc["w1_bytes_snapshot"]
        if origin == "BOOTSTRAP":
            assert sc["r1_status_path"] is None
            assert sc["r1_bytes_snapshot"] is None
            assert not (
                root
                / ".local"
                / "two-site-refactor"
                / "pipeline"
                / sc["request"]["run_prefix"]
                / "pre-review-tests-1.json"
            ).exists()
            assert not (
                root
                / ".local"
                / "two-site-refactor"
                / "proposals"
                / f"{sc['request']['run_prefix']}-reviewer-1"
            ).exists()
            assert sc["meta"].get("r1_id") is None
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
            assert sc["meta"].get("r1_id") is None
            receipt1_path = (
                root
                / ".local"
                / "two-site-refactor"
                / "pipeline"
                / sc["request"]["run_prefix"]
                / "pre-review-tests-1.json"
            )
            assert receipt1_path.is_file()
            receipt1_data = json.loads(receipt1_path.read_text(encoding="utf-8"))
            assert receipt1_data["state"] == "FAILED"
            assert receipt1_data["returncode"] != 0
            assert (
                hashlib.sha256(receipt1_path.read_bytes()).hexdigest()
                == json.loads(sc["response"]["stdout"])["pre_review_test_sha256"]
            )
        else:
            assert sc["r1_status_path"] is not None
            assert sc["r1_status_path"].read_bytes() == sc["r1_bytes_snapshot"]
        assert (
            hashlib.sha256(sc["orig_w2_status_path"].read_bytes()).hexdigest()
            == sc["orig_w2_status_sha"]
        )
