"""Integration tests for two-site accepted-lineage record with real cold broker evidence."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_quota_accepted_lineage_record import (
    PROTECTED_REF_NAMES,
    AcceptedLineageRecordError,
    build_accepted_lineage_record,
    validate_accepted_lineage_record,
)
from scripts.two_site_quota_final_acceptance_lineage import (
    collect_final_acceptance_upstream_lineage_from_snapshot_locked,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_final_acceptance_cold_snapshot import (
    _setup_snapshot_scenario,
)

_SAMPLE_PROTECTED_REFS = {
    "refs/heads/main": "1" * 40,
    "refs/remotes/origin/main": "2" * 40,
    "refs/heads/candidate/wa-ir-standby-v1": "3" * 40,
}


def _canonical_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _make_raw_and_enriched_results(
    state: str,
    source_head: str,
    head_after: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if state == "COMPLETE":
        raw_result = {
            "schema_version": "1.0",
            "state": "COMPLETE",
            "disposition": "NONE",
            "category": "NONE",
            "summary": "All steps verified cleanly with no tree mutation needed",
            "commit": None,
            "next_action": None,
            "blockers": [],
            "gemini_worker_run_id": None,
            "gemini_reviewer_run_id": None,
            "proposal_sha256": None,
        }
        head_changed = False
    elif state == "CONTINUE":
        raw_result = {
            "schema_version": "1.0",
            "state": "CONTINUE",
            "disposition": "NONE",
            "category": "NONE",
            "summary": "Work package applied with green build and verified test output",
            "commit": head_after,
            "next_action": "Proceed to follow-on verification task AE03-C5F",
            "blockers": [],
            "gemini_worker_run_id": "worker-run-cycle-123",
            "gemini_reviewer_run_id": "reviewer-run-cycle-123",
            "proposal_sha256": "e" * 64,
        }
        head_changed = True
    else:
        raise ValueError(f"unsupported test state: {state!r}")

    assert len(raw_result) == 11
    raw_bytes = _canonical_bytes(raw_result)
    result_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    enriched_result = dict(raw_result)
    enriched_result["head_before"] = source_head
    enriched_result["head_after"] = head_after
    enriched_result["head_changed"] = head_changed
    assert len(enriched_result) == 14

    return raw_result, enriched_result, result_sha256


def test_cold_lineage_positive_complete_unchanged_record(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    lock_path = data["lock_path"]
    root = data["root"]
    orig_run_id = data["controller_run_id"]
    claim_record = data["ctx"]["claim"]
    expected_snap_sha = hashlib.sha256(data["snapshot_file"].read_bytes()).hexdigest()

    with mock.patch(
        "scripts.two_site_provider_broker.execute_intent_bound_continuation",
        side_effect=AssertionError("provider continuation dispatch must not be called"),
    ) as spy_dispatch:
        with exclusive_supervisor_lock(lock_path):
            upstream = collect_final_acceptance_upstream_lineage_from_snapshot_locked(
                root=root,
                original_controller_run_id=orig_run_id,
            )

    assert spy_dispatch.call_count == 0
    assert upstream["continuation_controller_run_id"] == claim_record["continuation_controller_run_id"]
    assert upstream["assignment_id"] == claim_record["assignment_id"]
    assert upstream["assignment_sha256"] == claim_record["assignment_sha256"]
    assert upstream["source_head"] == claim_record["source_head"]
    assert upstream["expected_lineage_snapshot_sha256"] == expected_snap_sha
    assert upstream["response_state"] == "COMPLETED"
    assert upstream["exit_code"] == 0

    source_head = upstream["source_head"]
    raw_result, result, result_sha256 = _make_raw_and_enriched_results(
        state="COMPLETE",
        source_head=source_head,
        head_after=source_head,
    )

    acceptance_context = {
        "controller_run_id": upstream["continuation_controller_run_id"],
        "protected_refs": copy.deepcopy(_SAMPLE_PROTECTED_REFS),
        "assignment_id": upstream["assignment_id"],
        "assignment_sha256": upstream["assignment_sha256"],
        "current_head": source_head,
    }
    assert set(acceptance_context["protected_refs"].keys()) == PROTECTED_REF_NAMES

    record = build_accepted_lineage_record(
        result=result,
        acceptance_context=acceptance_context,
        upstream=upstream,
        result_sha256=result_sha256,
    )

    assert record["schema_version"] == "1.0"
    assert record["accepted_state"] == "COMPLETE"
    assert record["result_sha256"] == result_sha256
    assert record["head_before"] == source_head
    assert record["head_after"] == source_head
    assert record["head_changed"] is False
    assert record["controller_run_id"] == upstream["continuation_controller_run_id"]
    assert record["assignment_id"] == upstream["assignment_id"]
    assert record["assignment_sha256"] == upstream["assignment_sha256"]
    assert record["protected_refs"] == _SAMPLE_PROTECTED_REFS
    assert record["upstream"] == upstream

    validated = validate_accepted_lineage_record(
        record,
        expected_result=result,
        expected_context=acceptance_context,
        expected_upstream=upstream,
        expected_result_sha256=result_sha256,
    )
    assert validated == record

    # Detachment verification
    acceptance_context["protected_refs"]["refs/heads/main"] = "0" * 40
    upstream["task_id"] = "tampered-task-id"
    result["summary"] = "tampered-summary"
    assert record["protected_refs"]["refs/heads/main"] == "1" * 40
    assert record["upstream"]["task_id"] != "tampered-task-id"
    assert validated["protected_refs"]["refs/heads/main"] == "1" * 40


def test_cold_lineage_positive_continue_changed_record(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    with mock.patch(
        "scripts.two_site_provider_broker.execute_intent_bound_continuation",
        side_effect=AssertionError("provider continuation dispatch must not be called"),
    ) as spy_dispatch:
        with exclusive_supervisor_lock(data["lock_path"]):
            upstream = collect_final_acceptance_upstream_lineage_from_snapshot_locked(
                root=data["root"],
                original_controller_run_id=data["controller_run_id"],
            )

    assert spy_dispatch.call_count == 0
    source_head = upstream["source_head"]
    head_after = "d" * 40
    assert head_after != source_head

    raw_result, result, result_sha256 = _make_raw_and_enriched_results(
        state="CONTINUE",
        source_head=source_head,
        head_after=head_after,
    )
    assert result["commit"] == head_after
    assert result["next_action"] == "Proceed to follow-on verification task AE03-C5F"
    assert result["gemini_worker_run_id"] == "worker-run-cycle-123"
    assert result["gemini_reviewer_run_id"] == "reviewer-run-cycle-123"
    assert result["proposal_sha256"] == "e" * 64

    acceptance_context = {
        "controller_run_id": upstream["continuation_controller_run_id"],
        "protected_refs": copy.deepcopy(_SAMPLE_PROTECTED_REFS),
        "assignment_id": upstream["assignment_id"],
        "assignment_sha256": upstream["assignment_sha256"],
        "current_head": head_after,
    }

    record = build_accepted_lineage_record(
        result=result,
        acceptance_context=acceptance_context,
        upstream=upstream,
        result_sha256=result_sha256,
    )

    assert record["accepted_state"] == "CONTINUE"
    assert record["head_changed"] is True
    assert record["head_before"] == source_head
    assert record["head_after"] == head_after
    assert record["result_sha256"] == result_sha256
    assert record["controller_run_id"] == upstream["continuation_controller_run_id"]

    validated = validate_accepted_lineage_record(
        record,
        expected_result=result,
        expected_context=acceptance_context,
        expected_upstream=upstream,
        expected_result_sha256=result_sha256,
    )
    assert validated == record


@pytest.mark.parametrize(
    ("drift_field", "tampered_value", "match_err"),
    [
        (
            "controller_run_id",
            "cycle-9999-20260929T120000Z-999",
            "controller_run_id",
        ),
        (
            "assignment_id",
            "asgn-tampered-id-1234",
            "assignment_id",
        ),
        (
            "assignment_sha256",
            "9" * 64,
            "assignment_sha256",
        ),
        (
            "current_head",
            "0" * 40,
            "current_head",
        ),
    ],
)
def test_cold_lineage_context_drift_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    drift_field: str,
    tampered_value: str,
    match_err: str,
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    with exclusive_supervisor_lock(data["lock_path"]):
        upstream = collect_final_acceptance_upstream_lineage_from_snapshot_locked(
            root=data["root"],
            original_controller_run_id=data["controller_run_id"],
        )

    source_head = upstream["source_head"]
    _, result, result_sha256 = _make_raw_and_enriched_results(
        state="COMPLETE",
        source_head=source_head,
        head_after=source_head,
    )
    context = {
        "controller_run_id": upstream["continuation_controller_run_id"],
        "protected_refs": copy.deepcopy(_SAMPLE_PROTECTED_REFS),
        "assignment_id": upstream["assignment_id"],
        "assignment_sha256": upstream["assignment_sha256"],
        "current_head": source_head,
    }
    context[drift_field] = tampered_value

    with pytest.raises(AcceptedLineageRecordError, match=match_err):
        build_accepted_lineage_record(
            result=result,
            acceptance_context=context,
            upstream=upstream,
            result_sha256=result_sha256,
        )


def test_cold_lineage_result_head_drift_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    with exclusive_supervisor_lock(data["lock_path"]):
        upstream = collect_final_acceptance_upstream_lineage_from_snapshot_locked(
            root=data["root"],
            original_controller_run_id=data["controller_run_id"],
        )

    source_head = upstream["source_head"]
    alternate_head = "0" * 40
    assert alternate_head != source_head

    _, result, result_sha256 = _make_raw_and_enriched_results(
        state="COMPLETE",
        source_head=alternate_head,
        head_after=alternate_head,
    )
    context = {
        "controller_run_id": upstream["continuation_controller_run_id"],
        "protected_refs": copy.deepcopy(_SAMPLE_PROTECTED_REFS),
        "assignment_id": upstream["assignment_id"],
        "assignment_sha256": upstream["assignment_sha256"],
        "current_head": alternate_head,
    }

    with pytest.raises(AcceptedLineageRecordError, match="upstream source_head"):
        build_accepted_lineage_record(
            result=result,
            acceptance_context=context,
            upstream=upstream,
            result_sha256=result_sha256,
        )


def test_cold_lineage_protected_refs_tamper_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    with exclusive_supervisor_lock(data["lock_path"]):
        upstream = collect_final_acceptance_upstream_lineage_from_snapshot_locked(
            root=data["root"],
            original_controller_run_id=data["controller_run_id"],
        )

    source_head = upstream["source_head"]
    _, result, result_sha256 = _make_raw_and_enriched_results(
        state="COMPLETE",
        source_head=source_head,
        head_after=source_head,
    )

    # Adversarial branch substitution with valid hex
    bad_refs = {
        "refs/heads/main": "1" * 40,
        "refs/remotes/origin/main": "2" * 40,
        "refs/heads/adversarial-unprotected": "3" * 40,
    }
    context = {
        "controller_run_id": upstream["continuation_controller_run_id"],
        "protected_refs": bad_refs,
        "assignment_id": upstream["assignment_id"],
        "assignment_sha256": upstream["assignment_sha256"],
        "current_head": source_head,
    }

    with pytest.raises(AcceptedLineageRecordError, match="exact refs"):
        build_accepted_lineage_record(
            result=result,
            acceptance_context=context,
            upstream=upstream,
            result_sha256=result_sha256,
        )


def test_cold_lineage_raw_vs_enriched_result_sha_distinction(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    with exclusive_supervisor_lock(data["lock_path"]):
        upstream = collect_final_acceptance_upstream_lineage_from_snapshot_locked(
            root=data["root"],
            original_controller_run_id=data["controller_run_id"],
        )

    source_head = upstream["source_head"]
    _, result, raw_result_sha256 = _make_raw_and_enriched_results(
        state="COMPLETE",
        source_head=source_head,
        head_after=source_head,
    )
    context = {
        "controller_run_id": upstream["continuation_controller_run_id"],
        "protected_refs": copy.deepcopy(_SAMPLE_PROTECTED_REFS),
        "assignment_id": upstream["assignment_id"],
        "assignment_sha256": upstream["assignment_sha256"],
        "current_head": source_head,
    }

    # Compute SHA from 14-key enriched result instead of raw 11-key result
    enriched_bytes = _canonical_bytes(result)
    enriched_sha256 = hashlib.sha256(enriched_bytes).hexdigest()
    assert raw_result_sha256 != enriched_sha256

    record = build_accepted_lineage_record(
        result=result,
        acceptance_context=context,
        upstream=upstream,
        result_sha256=raw_result_sha256,
    )

    # Validating record expecting enriched sha fails closed
    with pytest.raises(AcceptedLineageRecordError, match="result_sha256"):
        validate_accepted_lineage_record(
            record,
            expected_result=result,
            expected_context=context,
            expected_upstream=upstream,
            expected_result_sha256=enriched_sha256,
        )
