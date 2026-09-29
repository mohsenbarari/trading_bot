"""Hermetic cold-source snapshot fault matrix for two-site final acceptance."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_quota_final_acceptance_lineage import (
    FinalAcceptanceLineageError,
    collect_final_acceptance_upstream_lineage_from_snapshot_locked,
)
import scripts.two_site_quota_resume_lineage_snapshot_storage
from scripts.two_site_quota_resume_lineage_snapshot_storage import (
    publish_expected_lineage_snapshot,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_final_acceptance_lineage import (
    _canonical_bytes,
    _setup_lineage_scenario,
    _write_canonical_record,
)


def _setup_snapshot_scenario(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> dict[str, Any]:
    data = _setup_lineage_scenario(broker_repo)
    ctx = data["ctx"]
    with exclusive_supervisor_lock(data["lock_path"]):
        published_snapshot = publish_expected_lineage_snapshot(
            root=data["root"],
            original_controller_run_id=data["controller_run_id"],
            wait_record=ctx["wait_record"],
            expected_wait=data["expected_wait"],
            claim_record=ctx["claim"],
            expected_claim=data["expected_claim"],
            request=data["expected_request"],
        )
    snapshot_file = ctx["runs_dir"] / "quota-resume-lineage-snapshot.json"
    data["published_snapshot"] = published_snapshot
    data["snapshot_file"] = snapshot_file
    return data


def test_cold_lineage_positive_exact_sha_and_immutability(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    status_file = data["ctx"]["runs_dir"] / "status.json"
    status_file.write_text(json.dumps({"state": "MUTABLE_STATUS_MUST_BE_IGNORED"}))

    evidence_files = (
        data["reservation_file"],
        data["wait_file"],
        data["claim_file"],
        data["submission_file"],
        data["terminal_file"],
        data["snapshot_file"],
        status_file,
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

    canonical_snap_sha = hashlib.sha256(snapshots[data["snapshot_file"]]).hexdigest()
    assert lineage["expected_lineage_snapshot_sha256"] == canonical_snap_sha
    assert lineage["response_state"] == "COMPLETED"
    assert lineage["exit_code"] == 0
    assert spy_dispatch.call_count == 0

    for f in evidence_files:
        assert f.read_bytes() == snapshots[f]


@pytest.mark.parametrize(
    ("fault_kind", "expected_match"),
    [
        ("missing_wait", "failed to read initial wait record"),
        ("corrupted_wait", "initial wait"),
        ("missing_claim", "failed to read known-reset resume claim"),
        ("corrupted_claim", "known-reset resume claim"),
        ("missing_submission", "failed to read submission intent"),
        ("corrupted_submission", "failed to read submission intent"),
        ("missing_terminal", "failed to read terminal continuation record"),
        ("corrupted_terminal", "failed to read terminal continuation record"),
    ],
)
def test_cold_lineage_independent_upstream_faults_fail_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    fault_kind: str,
    expected_match: str,
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    snap_bytes = data["snapshot_file"].read_bytes()

    if fault_kind == "missing_wait":
        data["wait_file"].unlink()
    elif fault_kind == "corrupted_wait":
        rec = json.loads(data["wait_file"].read_bytes())
        rec["request_id"] = "0" * 32
        _write_canonical_record(data["wait_file"], rec)
    elif fault_kind == "missing_claim":
        data["claim_file"].unlink()
    elif fault_kind == "corrupted_claim":
        rec = json.loads(data["claim_file"].read_bytes())
        rec["task_sha256"] = "0" * 64
        _write_canonical_record(data["claim_file"], rec)
    elif fault_kind == "missing_submission":
        data["submission_file"].unlink()
    elif fault_kind == "corrupted_submission":
        rec = json.loads(data["submission_file"].read_bytes())
        rec["request_sha256"] = "0" * 64
        _write_canonical_record(data["submission_file"], rec)
    elif fault_kind == "missing_terminal":
        data["terminal_file"].unlink()
    elif fault_kind == "corrupted_terminal":
        rec = json.loads(data["terminal_file"].read_bytes())
        rec["submission_intent_sha256"] = "0" * 64
        _write_canonical_record(data["terminal_file"], rec)

    with mock.patch(
        "scripts.two_site_provider_broker.execute_intent_bound_continuation",
        side_effect=AssertionError("provider continuation dispatch must not be called"),
    ) as spy_dispatch:
        with exclusive_supervisor_lock(data["lock_path"]):
            with pytest.raises(FinalAcceptanceLineageError, match=expected_match):
                collect_final_acceptance_upstream_lineage_from_snapshot_locked(
                    root=data["root"],
                    original_controller_run_id=data["controller_run_id"],
                )

    assert spy_dispatch.call_count == 0
    assert data["snapshot_file"].read_bytes() == snap_bytes


@pytest.mark.parametrize(
    ("mutation_kind", "expected_match"),
    [
        ("non_dict_candidate", "lineage snapshot candidate must be a dict"),
        ("missing_expected_wait", "snapshot expected_wait must be dict"),
        ("non_dict_expected_wait", "snapshot expected_wait must be dict"),
        ("missing_expected_claim", "snapshot expected_claim must be dict"),
        ("non_dict_expected_claim", "snapshot expected_claim must be dict"),
        ("missing_expected_request", "snapshot expected_request must be dict"),
        ("non_dict_expected_request", "snapshot expected_request must be dict"),
    ],
)
def test_cold_lineage_malformed_candidate_envelopes_fail_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    mutation_kind: str,
    expected_match: str,
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    snap_file = data["snapshot_file"]
    snap_obj = json.loads(snap_file.read_bytes())

    if mutation_kind == "non_dict_candidate":
        with mock.patch(
            "scripts.two_site_quota_final_acceptance_lineage.read_untrusted_expected_lineage_snapshot_candidate",
            return_value=["not", "a", "dict"],
        ):
            with exclusive_supervisor_lock(data["lock_path"]):
                with pytest.raises(FinalAcceptanceLineageError, match=expected_match):
                    collect_final_acceptance_upstream_lineage_from_snapshot_locked(
                        root=data["root"],
                        original_controller_run_id=data["controller_run_id"],
                    )
        return

    if mutation_kind == "missing_expected_wait":
        snap_obj.pop("expected_wait", None)
    elif mutation_kind == "non_dict_expected_wait":
        snap_obj["expected_wait"] = "not-a-dict"
    elif mutation_kind == "missing_expected_claim":
        snap_obj.pop("expected_claim", None)
    elif mutation_kind == "non_dict_expected_claim":
        snap_obj["expected_claim"] = 123
    elif mutation_kind == "missing_expected_request":
        snap_obj.pop("expected_request", None)
    elif mutation_kind == "non_dict_expected_request":
        snap_obj["expected_request"] = ["not", "a", "dict"]

    _write_canonical_record(snap_file, snap_obj)

    with mock.patch(
        "scripts.two_site_provider_broker.execute_intent_bound_continuation",
        side_effect=AssertionError("provider continuation dispatch must not be called"),
    ) as spy_dispatch:
        with exclusive_supervisor_lock(data["lock_path"]):
            with pytest.raises(FinalAcceptanceLineageError, match=expected_match):
                collect_final_acceptance_upstream_lineage_from_snapshot_locked(
                    root=data["root"],
                    original_controller_run_id=data["controller_run_id"],
                )

    assert spy_dispatch.call_count == 0


def test_cold_lineage_candidate_vs_strict_reread_mismatch_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    real_read_strict = scripts.two_site_quota_resume_lineage_snapshot_storage.read_expected_lineage_snapshot

    def _tampered_strict_snapshot(*args: Any, **kwargs: Any) -> dict[str, Any]:
        snap = dict(real_read_strict(*args, **kwargs))
        snap["request_sha256"] = "0" * 64
        return snap

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_lineage.read_expected_lineage_snapshot",
        side_effect=_tampered_strict_snapshot,
    ), mock.patch(
        "scripts.two_site_provider_broker.execute_intent_bound_continuation",
        side_effect=AssertionError("provider continuation dispatch must not be called"),
    ) as spy_dispatch:
        with exclusive_supervisor_lock(data["lock_path"]):
            with pytest.raises(
                FinalAcceptanceLineageError,
                match="candidate lineage snapshot does not match strict reread snapshot",
            ):
                collect_final_acceptance_upstream_lineage_from_snapshot_locked(
                    root=data["root"],
                    original_controller_run_id=data["controller_run_id"],
                )

    assert spy_dispatch.call_count == 0


def test_cold_lineage_candidate_cannot_bypass_independent_files(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    snap_file = data["snapshot_file"]
    snap_obj = json.loads(snap_file.read_bytes())
    snap_obj["expected_wait"]["request_id"] = "f" * 32
    _write_canonical_record(snap_file, snap_obj)

    with mock.patch(
        "scripts.two_site_provider_broker.execute_intent_bound_continuation",
        side_effect=AssertionError("provider continuation dispatch must not be called"),
    ) as spy_dispatch:
        with exclusive_supervisor_lock(data["lock_path"]):
            with pytest.raises(
                FinalAcceptanceLineageError,
                match="failed to read initial wait record",
            ):
                collect_final_acceptance_upstream_lineage_from_snapshot_locked(
                    root=data["root"],
                    original_controller_run_id=data["controller_run_id"],
                )

    assert spy_dispatch.call_count == 0


def test_cold_lineage_no_assumption_from_mutable_status_json(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    status_file = data["ctx"]["runs_dir"] / "status.json"

    rec = json.loads(data["terminal_file"].read_bytes())
    rec["response_sha256"] = "0" * 64
    _write_canonical_record(data["terminal_file"], rec)

    status_file.write_text(
        json.dumps({
            "state": "COMPLETED",
            "decision": "ACCEPTED",
            "exit_code": 0,
            "fake_authority": True,
        })
    )

    with mock.patch(
        "scripts.two_site_provider_broker.execute_intent_bound_continuation",
        side_effect=AssertionError("provider continuation dispatch must not be called"),
    ) as spy_dispatch:
        with exclusive_supervisor_lock(data["lock_path"]):
            with pytest.raises(
                FinalAcceptanceLineageError,
                match="failed to read terminal continuation record",
            ):
                collect_final_acceptance_upstream_lineage_from_snapshot_locked(
                    root=data["root"],
                    original_controller_run_id=data["controller_run_id"],
                )

    assert spy_dispatch.call_count == 0
