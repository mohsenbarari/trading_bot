"""Hermetic characterization tests for supervisor finalization composition boundary."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import copy
import json
from pathlib import Path
import stat
from typing import Any
from unittest import mock

import pytest

import scripts.two_site_quota_final_acceptance_bridge
import scripts.two_site_quota_final_ack_bridge
from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
from scripts.two_site_provider_broker import (
    _open_existing_cycle_run_directory,
)
from scripts.two_site_quota_accepted_lineage_storage import (
    ACCEPTED_LINEAGE_FILENAME,
)
from scripts.two_site_quota_final_acceptance_bridge import (
    FinalAcceptanceBridgeAmbiguousPostPublicationError,
    FinalAcceptanceBridgeError,
)
from scripts.two_site_quota_final_ack_bridge import (
    FinalAckBridgeError,
)
from scripts.two_site_quota_final_ack_storage import (
    FINAL_ACK_FILENAME,
)
from scripts.two_site_quota_supervisor_finalization import (
    SupervisorFinalizationAmbiguousError,
    finalize_accepted_quota_continuation_locked,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_accepted_lineage_integration import (
    _make_raw_and_enriched_results,
)
from tests.test_two_site_quota_final_acceptance_bridge import (
    _make_mock_repo_facts,
    _make_sample_scope,
    _prepare_bridge_fixture,
)
from tests.test_two_site_quota_final_acceptance_cold_snapshot import (
    _setup_snapshot_scenario,
)


def test_finalization_positive_continue(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    claim = data["ctx"]["claim"]
    source_head = claim["source_head"]
    head_after = "c" * 40
    assert head_after != source_head

    raw_res, enriched_res, _ = _make_raw_and_enriched_results(
        state="CONTINUE",
        source_head=source_head,
        head_after=head_after,
    )
    orig_id, cont_id, root = _prepare_bridge_fixture(data, raw_res)

    scope = _make_sample_scope(claim["assignment_id"], claim["assignment_sha256"])
    before_repo = _make_mock_repo_facts(source_head, scope)
    current_repo = _make_mock_repo_facts(head_after, scope)
    ack_time = "2026-09-29T12:05:00Z"

    call_order: list[str] = []
    real_lineage = (
        scripts.two_site_quota_final_acceptance_bridge.publish_validated_final_acceptance_lineage_locked
    )
    real_ack = (
        scripts.two_site_quota_final_ack_bridge.publish_validated_final_ack_locked
    )

    def _spy_lineage(*args: Any, **kwargs: Any) -> Any:
        call_order.append("lineage")
        return real_lineage(*args, **kwargs)

    def _spy_ack(*args: Any, **kwargs: Any) -> Any:
        call_order.append("ack")
        return real_ack(*args, **kwargs)

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_bridge.validate_repository",
        side_effect=[current_repo, current_repo, current_repo],
    ) as mock_val_acc, mock.patch(
        "scripts.two_site_quota_final_ack_bridge.validate_repository",
        side_effect=[current_repo, current_repo],
    ) as mock_val_ack, mock.patch(
        "scripts.two_site_quota_supervisor_finalization.publish_validated_final_acceptance_lineage_locked",
        side_effect=_spy_lineage,
    ), mock.patch(
        "scripts.two_site_quota_supervisor_finalization.publish_validated_final_ack_locked",
        side_effect=_spy_ack,
    ):
        with exclusive_supervisor_lock(data["lock_path"]):
            pub_ack = finalize_accepted_quota_continuation_locked(
                root=root,
                original_controller_run_id=orig_id,
                continuation_controller_run_id=cont_id,
                before_repository=before_repo,
                expected_transition_result=enriched_res,
                acknowledged_at_utc=ack_time,
            )

    assert call_order == ["lineage", "ack"]
    assert mock_val_acc.call_count == 3
    assert mock_val_ack.call_count == 2

    assert pub_ack["accepted_state"] == "CONTINUE"
    assert pub_ack["original_controller_run_id"] == orig_id
    assert pub_ack["continuation_controller_run_id"] == cont_id
    assert pub_ack["acknowledged_at_utc"] == ack_time
    assert pub_ack["head_after"] == head_after

    with _open_existing_cycle_run_directory(root, cont_id) as (run_path, _, _):
        lineage_file = run_path / ACCEPTED_LINEAGE_FILENAME
        ack_file = run_path / FINAL_ACK_FILENAME
        assert lineage_file.is_file()
        assert ack_file.is_file()
        assert stat.S_IMODE(os.stat(lineage_file).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(ack_file).st_mode) == 0o600

        disk_ack = json.loads(ack_file.read_text(encoding="utf-8"))
        assert disk_ack == pub_ack

        disk_lineage = json.loads(lineage_file.read_text(encoding="utf-8"))
        assert disk_lineage["accepted_state"] == "CONTINUE"
        assert disk_lineage["head_before"] == source_head
        assert disk_lineage["head_after"] == head_after
        assert disk_lineage["head_changed"] is True
        assert disk_lineage["controller_run_id"] == cont_id


def test_finalization_positive_complete(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    claim = data["ctx"]["claim"]
    source_head = claim["source_head"]

    raw_res, enriched_res, _ = _make_raw_and_enriched_results(
        state="COMPLETE",
        source_head=source_head,
        head_after=source_head,
    )
    orig_id, cont_id, root = _prepare_bridge_fixture(data, raw_res)

    scope = _make_sample_scope(claim["assignment_id"], claim["assignment_sha256"])
    repo_facts = _make_mock_repo_facts(source_head, scope)
    ack_time = "2026-09-29T12:05:00Z"

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_bridge.validate_repository",
        side_effect=[repo_facts, repo_facts, repo_facts],
    ) as mock_val_acc, mock.patch(
        "scripts.two_site_quota_final_ack_bridge.validate_repository",
        side_effect=[repo_facts, repo_facts],
    ) as mock_val_ack:
        with exclusive_supervisor_lock(data["lock_path"]):
            pub_ack = finalize_accepted_quota_continuation_locked(
                root=root,
                original_controller_run_id=orig_id,
                continuation_controller_run_id=cont_id,
                before_repository=repo_facts,
                expected_transition_result=enriched_res,
                acknowledged_at_utc=ack_time,
            )

    assert mock_val_acc.call_count == 3
    assert mock_val_ack.call_count == 2

    assert pub_ack["accepted_state"] == "COMPLETE"
    assert pub_ack["original_controller_run_id"] == orig_id
    assert pub_ack["continuation_controller_run_id"] == cont_id
    assert pub_ack["acknowledged_at_utc"] == ack_time
    assert pub_ack["head_after"] == source_head

    with _open_existing_cycle_run_directory(root, cont_id) as (run_path, _, _):
        lineage_file = run_path / ACCEPTED_LINEAGE_FILENAME
        ack_file = run_path / FINAL_ACK_FILENAME
        assert lineage_file.is_file()
        assert ack_file.is_file()
        assert stat.S_IMODE(os.stat(lineage_file).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(ack_file).st_mode) == 0o600

        disk_ack = json.loads(ack_file.read_text(encoding="utf-8"))
        assert disk_ack == pub_ack

        disk_lineage = json.loads(lineage_file.read_text(encoding="utf-8"))
        assert disk_lineage["accepted_state"] == "COMPLETE"
        assert disk_lineage["head_before"] == source_head
        assert disk_lineage["head_after"] == source_head
        assert disk_lineage["head_changed"] is False
        assert disk_lineage["controller_run_id"] == cont_id


def test_finalization_failure_before_lineage_no_ack(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    claim = data["ctx"]["claim"]
    source_head = claim["source_head"]

    raw_res, enriched_res, _ = _make_raw_and_enriched_results(
        state="COMPLETE",
        source_head=source_head,
        head_after=source_head,
    )
    orig_id, cont_id, root = _prepare_bridge_fixture(data, raw_res)

    scope = _make_sample_scope(claim["assignment_id"], claim["assignment_sha256"])
    drifted_before = _make_mock_repo_facts("0" * 40, scope)

    with mock.patch(
        "scripts.two_site_quota_supervisor_finalization.publish_validated_final_ack_locked",
        side_effect=AssertionError("ack bridge must never be called on lineage failure"),
    ) as mock_ack:
        with exclusive_supervisor_lock(data["lock_path"]):
            with pytest.raises(
                FinalAcceptanceBridgeError,
                match="head_before",
            ):
                finalize_accepted_quota_continuation_locked(
                    root=root,
                    original_controller_run_id=orig_id,
                    continuation_controller_run_id=cont_id,
                    before_repository=drifted_before,
                    expected_transition_result=enriched_res,
                    acknowledged_at_utc="2026-09-29T12:05:00Z",
                )

    assert mock_ack.call_count == 0
    with _open_existing_cycle_run_directory(root, cont_id) as (run_path, _, _):
        assert not (run_path / ACCEPTED_LINEAGE_FILENAME).exists()
        assert not (run_path / FINAL_ACK_FILENAME).exists()


def test_finalization_failure_between_lineage_and_ack(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    claim = data["ctx"]["claim"]
    source_head = claim["source_head"]

    raw_res, enriched_res, _ = _make_raw_and_enriched_results(
        state="COMPLETE",
        source_head=source_head,
        head_after=source_head,
    )
    orig_id, cont_id, root = _prepare_bridge_fixture(data, raw_res)

    scope = _make_sample_scope(claim["assignment_id"], claim["assignment_sha256"])
    repo_facts = _make_mock_repo_facts(source_head, scope)

    injected_ack_error = FinalAckBridgeError("injected ack failure between bridges")

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_bridge.validate_repository",
        side_effect=[repo_facts, repo_facts, repo_facts],
    ), mock.patch(
        "scripts.two_site_quota_supervisor_finalization.publish_validated_final_ack_locked",
        side_effect=injected_ack_error,
    ) as mock_ack:
        with exclusive_supervisor_lock(data["lock_path"]):
            with pytest.raises(SupervisorFinalizationAmbiguousError) as exc_info:
                finalize_accepted_quota_continuation_locked(
                    root=root,
                    original_controller_run_id=orig_id,
                    continuation_controller_run_id=cont_id,
                    before_repository=repo_facts,
                    expected_transition_result=enriched_res,
                    acknowledged_at_utc="2026-09-29T12:05:00Z",
                )

    assert mock_ack.call_count == 1
    assert exc_info.value.__cause__ is injected_ack_error

    with _open_existing_cycle_run_directory(root, cont_id) as (run_path, _, _):
        lineage_file = run_path / ACCEPTED_LINEAGE_FILENAME
        ack_file = run_path / FINAL_ACK_FILENAME
        assert lineage_file.is_file()
        assert not ack_file.exists()

        initial_bytes = lineage_file.read_bytes()
        initial_st = os.stat(lineage_file)

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_bridge.validate_repository",
        return_value=repo_facts,
    ):
        with exclusive_supervisor_lock(data["lock_path"]):
            with pytest.raises(
                FinalAcceptanceBridgeAmbiguousPostPublicationError,
                match="already exists",
            ):
                finalize_accepted_quota_continuation_locked(
                    root=root,
                    original_controller_run_id=orig_id,
                    continuation_controller_run_id=cont_id,
                    before_repository=repo_facts,
                    expected_transition_result=enriched_res,
                    acknowledged_at_utc="2026-09-29T12:06:00Z",
                )

    with _open_existing_cycle_run_directory(root, cont_id) as (run_path, _, _):
        assert lineage_file.read_bytes() == initial_bytes
        second_st = os.stat(lineage_file)
        assert second_st.st_ino == initial_st.st_ino
        assert second_st.st_mtime_ns == initial_st.st_mtime_ns
        assert not ack_file.exists()


@pytest.mark.parametrize(
    "fault",
    [
        KeyboardInterrupt("injected keyboard interrupt in ack"),
        SystemExit(2),
    ],
)
def test_finalization_base_exception_retention(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    fault: BaseException,
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    claim = data["ctx"]["claim"]
    source_head = claim["source_head"]

    raw_res, enriched_res, _ = _make_raw_and_enriched_results(
        state="COMPLETE",
        source_head=source_head,
        head_after=source_head,
    )
    orig_id, cont_id, root = _prepare_bridge_fixture(data, raw_res)

    scope = _make_sample_scope(claim["assignment_id"], claim["assignment_sha256"])
    repo_facts = _make_mock_repo_facts(source_head, scope)

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_bridge.validate_repository",
        side_effect=[repo_facts, repo_facts, repo_facts],
    ), mock.patch(
        "scripts.two_site_quota_supervisor_finalization.publish_validated_final_ack_locked",
        side_effect=fault,
    ):
        with exclusive_supervisor_lock(data["lock_path"]):
            with pytest.raises(SupervisorFinalizationAmbiguousError) as exc_info:
                finalize_accepted_quota_continuation_locked(
                    root=root,
                    original_controller_run_id=orig_id,
                    continuation_controller_run_id=cont_id,
                    before_repository=repo_facts,
                    expected_transition_result=enriched_res,
                    acknowledged_at_utc="2026-09-29T12:05:00Z",
                )

    assert exc_info.value.__cause__ is fault
    with _open_existing_cycle_run_directory(root, cont_id) as (run_path, _, _):
        lineage_file = run_path / ACCEPTED_LINEAGE_FILENAME
        assert lineage_file.is_file()
        assert not (run_path / FINAL_ACK_FILENAME).exists()


def test_finalization_lineage_ambiguous_preserved(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    claim = data["ctx"]["claim"]
    source_head = claim["source_head"]

    raw_res, enriched_res, _ = _make_raw_and_enriched_results(
        state="COMPLETE",
        source_head=source_head,
        head_after=source_head,
    )
    orig_id, cont_id, root = _prepare_bridge_fixture(data, raw_res)

    scope = _make_sample_scope(claim["assignment_id"], claim["assignment_sha256"])
    repo_facts = _make_mock_repo_facts(source_head, scope)
    drifted_post_pub = copy.deepcopy(repo_facts)
    drifted_post_pub["head"] = "7" * 40

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_bridge.validate_repository",
        side_effect=[repo_facts, repo_facts, drifted_post_pub],
    ), mock.patch(
        "scripts.two_site_quota_supervisor_finalization.publish_validated_final_ack_locked",
        side_effect=AssertionError("ack bridge must never be called on lineage failure"),
    ) as mock_ack:
        with exclusive_supervisor_lock(data["lock_path"]):
            with pytest.raises(
                FinalAcceptanceBridgeAmbiguousPostPublicationError,
                match="ambiguous post-publication failure",
            ):
                finalize_accepted_quota_continuation_locked(
                    root=root,
                    original_controller_run_id=orig_id,
                    continuation_controller_run_id=cont_id,
                    before_repository=repo_facts,
                    expected_transition_result=enriched_res,
                    acknowledged_at_utc="2026-09-29T12:05:00Z",
                )

    assert mock_ack.call_count == 0
    with _open_existing_cycle_run_directory(root, cont_id) as (run_path, _, _):
        lineage_file = run_path / ACCEPTED_LINEAGE_FILENAME
        assert lineage_file.is_file()
        assert not (run_path / FINAL_ACK_FILENAME).exists()
