"""Hermetic upstream lineage collector tests for two-site final acceptance."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_quota_continuation_response import (
    build_continuation_broker_response,
)
from scripts.two_site_quota_continuation_terminal_storage import (
    publish_terminal_continuation_record,
)
from scripts.two_site_quota_final_acceptance_lineage import (
    FinalAcceptanceLineageError,
    collect_final_acceptance_upstream_lineage_from_snapshot_locked,
    collect_final_acceptance_upstream_lineage_locked,
)
from scripts.two_site_quota_resume_lineage_snapshot_storage import (
    publish_expected_lineage_snapshot,
)
from scripts.two_site_quota_resume_submission_intent import (
    compute_canonical_request_sha256,
    publish_quota_resume_submission_intent,
)
from scripts.two_site_quota_wait_sources import (
    collect_initial_wait_source_expected,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_admission import (
    _setup_w2_admission_claim_scenario,
)

_EXPECTED_LINEAGE_KEYS = {
    "original_controller_run_id",
    "continuation_controller_run_id",
    "original_request_id",
    "resume_request_id",
    "task_id",
    "assignment_id",
    "source_head",
    "task_sha256",
    "assignment_sha256",
    "semantic_task_fingerprint",
    "original_wait_sha256",
    "claim_sha256",
    "request_sha256",
    "submission_intent_sha256",
    "terminal_receipt_sha256",
    "response_sha256",
    "response_state",
    "exit_code",
}


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _write_canonical_record(path: Path, payload: Any) -> None:
    path.write_bytes(_canonical_bytes(payload))
    os.chmod(path, 0o600)


def _setup_lineage_scenario(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> dict[str, Any]:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)
    root = ctx["root"]
    sc = ctx["sc"]
    controller_run_id = ctx["controller_run_id"]
    resume_request = ctx["resume_request"]
    expected_claim = ctx["expected_claim"]
    now_epoch = ctx["now_epoch"]

    with exclusive_supervisor_lock(sc["lock_path"]):
        expected_wait = collect_initial_wait_source_expected(
            root,
            controller_run_id,
            sc["rescope_epoch"],
            1,
        )
        publish_quota_resume_submission_intent(
            root,
            resume_request,
            now_epoch=now_epoch,
        )

    response = build_continuation_broker_response(
        resume_request,
        {"state": "READY_FOR_CODEX"},
        now_epoch=now_epoch,
    )
    receipt = publish_terminal_continuation_record(root, resume_request, response)

    submission_file = ctx["runs_dir"] / "quota-resume-submission.json"
    terminal_file = (
        root
        / ".local"
        / "two-site-refactor"
        / "provider-broker"
        / "terminal-continuation"
        / f"{resume_request['request_id']}.json"
    )

    return {
        "ctx": ctx,
        "root": root,
        "sc": sc,
        "controller_run_id": controller_run_id,
        "expected_wait": expected_wait,
        "expected_claim": expected_claim,
        "expected_request": resume_request,
        "response": response,
        "receipt": receipt,
        "reservation_file": sc["reservation_file"],
        "wait_file": ctx["wait_file"],
        "claim_file": ctx["claim_file"],
        "submission_file": submission_file,
        "terminal_file": terminal_file,
        "lock_path": sc["lock_path"],
    }


def test_two_site_quota_final_acceptance_lineage_positive(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_lineage_scenario(broker_repo)
    evidence_files = (
        data["reservation_file"],
        data["wait_file"],
        data["claim_file"],
        data["submission_file"],
        data["terminal_file"],
    )
    snapshots = {f: f.read_bytes() for f in evidence_files}

    with mock.patch(
        "scripts.two_site_provider_broker.execute_intent_bound_continuation",
        side_effect=AssertionError("provider continuation dispatch must not be called"),
    ) as spy_dispatch:
        with exclusive_supervisor_lock(data["lock_path"]):
            lineage = collect_final_acceptance_upstream_lineage_locked(
                root=data["root"],
                original_controller_run_id=data["controller_run_id"],
                expected_wait=data["expected_wait"],
                expected_claim=data["expected_claim"],
                expected_request=data["expected_request"],
            )

    assert set(lineage.keys()) == _EXPECTED_LINEAGE_KEYS
    assert lineage["original_controller_run_id"] == data["controller_run_id"]
    assert lineage["continuation_controller_run_id"] == data["ctx"]["continuation_controller_run_id"]
    assert lineage["original_request_id"] == data["ctx"]["wait_record"]["request_id"]
    assert lineage["resume_request_id"] == data["expected_request"]["request_id"]
    assert lineage["task_id"] == data["ctx"]["real_task"]["task_id"]
    assert lineage["assignment_id"] == data["expected_claim"]["assignment_id"]
    assert lineage["source_head"] == data["expected_claim"]["source_head"]
    assert lineage["task_sha256"] == data["expected_claim"]["task_sha256"]
    assert lineage["assignment_sha256"] == data["expected_claim"]["assignment_sha256"]
    assert lineage["semantic_task_fingerprint"] == data["expected_claim"]["semantic_task_fingerprint"]
    assert lineage["original_wait_sha256"] == data["ctx"]["orig_wait_sha256"]
    assert lineage["claim_sha256"] == data["ctx"]["claim_sha256"]
    assert lineage["request_sha256"] == compute_canonical_request_sha256(data["expected_request"])
    assert lineage["submission_intent_sha256"] == hashlib.sha256(snapshots[data["submission_file"]]).hexdigest()
    assert lineage["terminal_receipt_sha256"] == hashlib.sha256(snapshots[data["terminal_file"]]).hexdigest()

    canonical_resp_bytes = _canonical_bytes(data["response"])
    assert lineage["response_sha256"] == hashlib.sha256(canonical_resp_bytes).hexdigest()
    assert lineage["response_state"] == "COMPLETED"
    assert lineage["exit_code"] == 0

    for f in evidence_files:
        assert f.read_bytes() == snapshots[f]
    assert spy_dispatch.call_count == 0


@pytest.mark.parametrize(
    "artifact_name",
    [
        "reservation_file",
        "wait_file",
        "claim_file",
        "submission_file",
        "terminal_file",
    ],
)
def test_missing_upstream_artifact_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    artifact_name: str,
) -> None:
    data = _setup_lineage_scenario(broker_repo)
    target = data[artifact_name]
    target.unlink()
    assert not target.exists()

    with exclusive_supervisor_lock(data["lock_path"]):
        with pytest.raises(FinalAcceptanceLineageError):
            collect_final_acceptance_upstream_lineage_locked(
                root=data["root"],
                original_controller_run_id=data["controller_run_id"],
                expected_wait=data["expected_wait"],
                expected_claim=data["expected_claim"],
                expected_request=data["expected_request"],
            )


@pytest.mark.parametrize(
    ("mutation_kind",),
    [
        ("invalid_run_id",),
        ("wrong_root_type",),
        ("mismatched_expected_wait_req_id",),
        ("mismatched_expected_claim_wait_sha",),
        ("mismatched_expected_claim_role",),
        ("mismatched_expected_request_claim_sha",),
        ("mismatched_expected_request_wait_sha",),
        ("invalid_expected_request_created_at",),
    ],
)
def test_mismatched_or_invalid_expected_identities_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    mutation_kind: str,
) -> None:
    data = _setup_lineage_scenario(broker_repo)
    root: Any = data["root"]
    controller_run_id = data["controller_run_id"]
    expected_wait = dict(data["expected_wait"])
    expected_claim = dict(data["expected_claim"])
    expected_request = dict(data["expected_request"])

    if mutation_kind == "invalid_run_id":
        controller_run_id = "not-a-cycle-run-id"
    elif mutation_kind == "wrong_root_type":
        root = str(root)
    elif mutation_kind == "mismatched_expected_wait_req_id":
        expected_wait["request_id"] = "f" * 32
    elif mutation_kind == "mismatched_expected_claim_wait_sha":
        expected_claim["original_wait_sha256"] = "0" * 64
    elif mutation_kind == "mismatched_expected_claim_role":
        expected_claim["role"] = "WRONG_ROLE"
    elif mutation_kind == "mismatched_expected_request_claim_sha":
        expected_request["claim_sha256"] = "0" * 64
    elif mutation_kind == "mismatched_expected_request_wait_sha":
        expected_request["original_wait_sha256"] = "0" * 64
    elif mutation_kind == "invalid_expected_request_created_at":
        expected_request["created_at"] = "not-numeric"

    with exclusive_supervisor_lock(data["lock_path"]):
        with pytest.raises(FinalAcceptanceLineageError):
            collect_final_acceptance_upstream_lineage_locked(
                root=root,
                original_controller_run_id=controller_run_id,
                expected_wait=expected_wait,
                expected_claim=expected_claim,
                expected_request=expected_request,
            )


@pytest.mark.parametrize(
    ("artifact_mutation",),
    [
        ("corrupt_terminal_response_sha",),
        ("tampered_terminal_response_state",),
        ("tampered_terminal_exit_code",),
        ("tampered_terminal_intent_sha",),
        ("tampered_submission_request_sha",),
        ("tampered_reservation_content",),
    ],
)
def test_conflicting_fields_or_hashes_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    artifact_mutation: str,
) -> None:
    data = _setup_lineage_scenario(broker_repo)

    if artifact_mutation == "corrupt_terminal_response_sha":
        rec = json.loads(data["terminal_file"].read_bytes())
        rec["response_sha256"] = "0" * 64
        _write_canonical_record(data["terminal_file"], rec)
    elif artifact_mutation == "tampered_terminal_response_state":
        rec = json.loads(data["terminal_file"].read_bytes())
        rec["response"]["state"] = "FAILED"
        rec["response_sha256"] = hashlib.sha256(
            _canonical_bytes(rec["response"])
        ).hexdigest()
        _write_canonical_record(data["terminal_file"], rec)
    elif artifact_mutation == "tampered_terminal_exit_code":
        rec = json.loads(data["terminal_file"].read_bytes())
        rec["response"]["exit_code"] = 2
        rec["response_sha256"] = hashlib.sha256(
            _canonical_bytes(rec["response"])
        ).hexdigest()
        _write_canonical_record(data["terminal_file"], rec)
    elif artifact_mutation == "tampered_terminal_intent_sha":
        rec = json.loads(data["terminal_file"].read_bytes())
        rec["submission_intent_sha256"] = "0" * 64
        _write_canonical_record(data["terminal_file"], rec)
    elif artifact_mutation == "tampered_submission_request_sha":
        sub = json.loads(data["submission_file"].read_bytes())
        sub["request_sha256"] = "0" * 64
        _write_canonical_record(data["submission_file"], sub)
    elif artifact_mutation == "tampered_reservation_content":
        res = json.loads(data["reservation_file"].read_bytes())
        res["reservation"]["source_head"] = "0" * 40
        _write_canonical_record(data["reservation_file"], res)

    with exclusive_supervisor_lock(data["lock_path"]):
        with pytest.raises(FinalAcceptanceLineageError):
            collect_final_acceptance_upstream_lineage_locked(
                root=data["root"],
                original_controller_run_id=data["controller_run_id"],
                expected_wait=data["expected_wait"],
                expected_claim=data["expected_claim"],
                expected_request=data["expected_request"],
            )


def test_simulated_between_read_wait_byte_mismatch_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_lineage_scenario(broker_repo)
    orig_bytes = data["ctx"]["raw_wait_bytes"]

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_lineage.read_initial_wait_record_bytes",
        return_value=orig_bytes + b" ",
    ):
        with exclusive_supervisor_lock(data["lock_path"]):
            with pytest.raises(
                FinalAcceptanceLineageError,
                match="raw initial wait bytes do not match canonical encoding",
            ):
                collect_final_acceptance_upstream_lineage_locked(
                    root=data["root"],
                    original_controller_run_id=data["controller_run_id"],
                    expected_wait=data["expected_wait"],
                    expected_claim=data["expected_claim"],
                    expected_request=data["expected_request"],
                )


@pytest.mark.parametrize(
    "artifact_name",
    ["wait_file", "claim_file", "submission_file", "terminal_file"],
)
def test_symlink_evidence_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    artifact_name: str,
) -> None:
    data = _setup_lineage_scenario(broker_repo)
    target = data[artifact_name]
    real_target = target.with_name(f"{target.name}.real")
    target.rename(real_target)
    target.symlink_to(real_target)
    assert target.is_symlink()

    with exclusive_supervisor_lock(data["lock_path"]):
        with pytest.raises(FinalAcceptanceLineageError):
            collect_final_acceptance_upstream_lineage_locked(
                root=data["root"],
                original_controller_run_id=data["controller_run_id"],
                expected_wait=data["expected_wait"],
                expected_claim=data["expected_claim"],
                expected_request=data["expected_request"],
            )


@pytest.mark.parametrize(
    "artifact_name",
    ["wait_file", "claim_file", "submission_file", "terminal_file"],
)
def test_hardlink_evidence_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    artifact_name: str,
) -> None:
    data = _setup_lineage_scenario(broker_repo)
    target = data[artifact_name]
    hardlink_copy = target.with_name(f"{target.name}.hardlink")
    os.link(target, hardlink_copy)
    assert target.stat().st_nlink > 1

    with exclusive_supervisor_lock(data["lock_path"]):
        with pytest.raises(FinalAcceptanceLineageError):
            collect_final_acceptance_upstream_lineage_locked(
                root=data["root"],
                original_controller_run_id=data["controller_run_id"],
                expected_wait=data["expected_wait"],
                expected_claim=data["expected_claim"],
                expected_request=data["expected_request"],
            )


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit])
def test_keyboard_interrupt_and_system_exit_propagate(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    exc_type: type[BaseException],
) -> None:
    data = _setup_lineage_scenario(broker_repo)

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_lineage.read_cycle_reservation",
        side_effect=exc_type,
    ):
        with exclusive_supervisor_lock(data["lock_path"]):
            with pytest.raises(exc_type):
                collect_final_acceptance_upstream_lineage_locked(
                    root=data["root"],
                    original_controller_run_id=data["controller_run_id"],
                    expected_wait=data["expected_wait"],
                    expected_claim=data["expected_claim"],
                    expected_request=data["expected_request"],
                )


def test_two_site_quota_final_acceptance_from_snapshot_positive(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_lineage_scenario(broker_repo)
    ctx = data["ctx"]
    claim = ctx["claim"]
    wait_record = ctx["wait_record"]

    with exclusive_supervisor_lock(data["lock_path"]):
        published_snapshot = publish_expected_lineage_snapshot(
            root=data["root"],
            original_controller_run_id=data["controller_run_id"],
            wait_record=wait_record,
            expected_wait=data["expected_wait"],
            claim_record=claim,
            expected_claim=data["expected_claim"],
            request=data["expected_request"],
        )

    expected_snap_sha256 = hashlib.sha256(
        _canonical_bytes(published_snapshot)
    ).hexdigest()

    snapshot_file = ctx["runs_dir"] / "quota-resume-lineage-snapshot.json"
    evidence_files = (
        data["reservation_file"],
        data["wait_file"],
        data["claim_file"],
        data["submission_file"],
        data["terminal_file"],
        snapshot_file,
    )
    snapshots = {f: f.read_bytes() for f in evidence_files}

    with mock.patch(
        "scripts.two_site_provider_broker.execute_intent_bound_continuation",
        side_effect=AssertionError("provider continuation dispatch must not be called"),
    ) as spy_dispatch:
        with exclusive_supervisor_lock(data["lock_path"]):
            lineage = collect_final_acceptance_upstream_lineage_from_snapshot_locked(
                root=data["root"],
                original_controller_run_id=data["controller_run_id"],
            )

    expected_keys = _EXPECTED_LINEAGE_KEYS | {"expected_lineage_snapshot_sha256"}
    assert set(lineage.keys()) == expected_keys
    assert lineage["expected_lineage_snapshot_sha256"] == expected_snap_sha256

    assert lineage["original_controller_run_id"] == data["controller_run_id"]
    assert lineage["continuation_controller_run_id"] == data["ctx"]["continuation_controller_run_id"]
    assert lineage["original_request_id"] == data["ctx"]["wait_record"]["request_id"]
    assert lineage["resume_request_id"] == data["expected_request"]["request_id"]
    assert lineage["task_id"] == data["ctx"]["real_task"]["task_id"]
    assert lineage["assignment_id"] == data["expected_claim"]["assignment_id"]
    assert lineage["source_head"] == data["expected_claim"]["source_head"]
    assert lineage["task_sha256"] == data["expected_claim"]["task_sha256"]
    assert lineage["assignment_sha256"] == data["expected_claim"]["assignment_sha256"]
    assert lineage["semantic_task_fingerprint"] == data["expected_claim"]["semantic_task_fingerprint"]
    assert lineage["original_wait_sha256"] == data["ctx"]["orig_wait_sha256"]
    assert lineage["claim_sha256"] == data["ctx"]["claim_sha256"]
    assert lineage["request_sha256"] == compute_canonical_request_sha256(data["expected_request"])
    assert lineage["submission_intent_sha256"] == hashlib.sha256(snapshots[data["submission_file"]]).hexdigest()
    assert lineage["terminal_receipt_sha256"] == hashlib.sha256(snapshots[data["terminal_file"]]).hexdigest()

    canonical_resp_bytes = _canonical_bytes(data["response"])
    assert lineage["response_sha256"] == hashlib.sha256(canonical_resp_bytes).hexdigest()
    assert lineage["response_state"] == "COMPLETED"
    assert lineage["exit_code"] == 0

    for f in evidence_files:
        assert f.read_bytes() == snapshots[f]
    assert spy_dispatch.call_count == 0


def test_cold_collector_missing_or_tampered_snapshot_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_lineage_scenario(broker_repo)

    with exclusive_supervisor_lock(data["lock_path"]):
        with pytest.raises(FinalAcceptanceLineageError):
            collect_final_acceptance_upstream_lineage_from_snapshot_locked(
                root=data["root"],
                original_controller_run_id=data["controller_run_id"],
            )

    ctx = data["ctx"]
    claim = ctx["claim"]
    with exclusive_supervisor_lock(data["lock_path"]):
        publish_expected_lineage_snapshot(
            root=data["root"],
            original_controller_run_id=data["controller_run_id"],
            wait_record=ctx["wait_record"],
            expected_wait=data["expected_wait"],
            claim_record=claim,
            expected_claim=data["expected_claim"],
            request=data["expected_request"],
        )

    snapshot_file = ctx["runs_dir"] / "quota-resume-lineage-snapshot.json"
    snap_obj = json.loads(snapshot_file.read_bytes())
    snap_obj["request_sha256"] = "0" * 64
    _write_canonical_record(snapshot_file, snap_obj)

    with exclusive_supervisor_lock(data["lock_path"]):
        with pytest.raises(FinalAcceptanceLineageError):
            collect_final_acceptance_upstream_lineage_from_snapshot_locked(
                root=data["root"],
                original_controller_run_id=data["controller_run_id"],
            )
