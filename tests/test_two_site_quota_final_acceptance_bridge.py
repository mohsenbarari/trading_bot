"""Unit and fault characterization tests for final-acceptance bridge."""

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
from typing import Any
from unittest import mock

import pytest

from scripts.two_site_assignment_scope import AssignmentScope
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
    publish_validated_final_acceptance_lineage_locked,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_accepted_lineage_integration import (
    _SAMPLE_PROTECTED_REFS,
    _make_raw_and_enriched_results,
)
from tests.test_two_site_quota_accepted_lineage_storage import _create_cycle_dir
from tests.test_two_site_quota_final_acceptance_cold_snapshot import (
    _setup_snapshot_scenario,
)


def _make_sample_scope(
    assignment_id: str,
    assignment_sha256: str,
    stage_id: str = "stage-c5e",
    allowed_paths: tuple[str, ...] = ("scripts/two_site_quota_final_acceptance_bridge.py",),
    forbidden_paths: tuple[str, ...] = (),
    expires_at: str = "2026-10-01T00:00:00Z",
) -> AssignmentScope:
    return AssignmentScope(
        assignment_id=assignment_id,
        stage_id=stage_id,
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
        expires_at=expires_at,
        sha256=assignment_sha256,
    )


def _make_mock_repo_facts(
    head: str,
    scope: AssignmentScope,
    protected_refs: dict[str, str] | None = None,
) -> dict[str, Any]:
    if protected_refs is None:
        protected_refs = copy.deepcopy(_SAMPLE_PROTECTED_REFS)
    return {
        "head": head,
        "status": "clean",
        "protected_refs": copy.deepcopy(protected_refs),
        "control_blobs": {},
        "assignment_scope": scope,
    }


def _canonical_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _prepare_bridge_fixture(
    data: dict[str, Any],
    raw_result: dict[str, Any],
) -> tuple[str, str, Path]:
    root = data["root"]
    orig_run_id = data["controller_run_id"]
    cont_run_id = data["ctx"]["claim"]["continuation_controller_run_id"]

    cont_dir = _create_cycle_dir(root, cont_run_id)
    result_file = cont_dir / "result.json"
    result_file.write_bytes(_canonical_bytes(raw_result))
    os.chmod(result_file, 0o600)

    return orig_run_id, cont_run_id, root


def test_bridge_positive_continue(
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

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_bridge.validate_repository",
        side_effect=[current_repo, current_repo, current_repo],
    ) as mock_val:
        with exclusive_supervisor_lock(data["lock_path"]):
            published = publish_validated_final_acceptance_lineage_locked(
                root=root,
                original_controller_run_id=orig_id,
                continuation_controller_run_id=cont_id,
                before_repository=before_repo,
                expected_transition_result=enriched_res,
            )

    assert mock_val.call_count == 3
    assert published["accepted_state"] == "CONTINUE"
    assert published["head_before"] == source_head
    assert published["head_after"] == head_after
    assert published["head_changed"] is True
    assert published["controller_run_id"] == cont_id

    with _open_existing_cycle_run_directory(root, cont_id) as (run_path, _, _):
        target_file = run_path / ACCEPTED_LINEAGE_FILENAME
        assert target_file.is_file()


def test_bridge_positive_complete(
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

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_bridge.validate_repository",
        side_effect=[repo_facts, repo_facts, repo_facts],
    ) as mock_val:
        with exclusive_supervisor_lock(data["lock_path"]):
            published = publish_validated_final_acceptance_lineage_locked(
                root=root,
                original_controller_run_id=orig_id,
                continuation_controller_run_id=cont_id,
                before_repository=repo_facts,
                expected_transition_result=enriched_res,
            )

    assert mock_val.call_count == 3
    assert published["accepted_state"] == "COMPLETE"
    assert published["head_changed"] is False
    assert published["head_before"] == source_head
    assert published["head_after"] == source_head


def test_bridge_original_continuation_mismatch(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    data = _setup_snapshot_scenario(broker_repo)
    claim = data["ctx"]["claim"]
    source_head = claim["source_head"]
    _, enriched_res, _ = _make_raw_and_enriched_results(
        state="COMPLETE",
        source_head=source_head,
        head_after=source_head,
    )
    root = data["root"]
    orig_id = data["controller_run_id"]
    scope = _make_sample_scope(claim["assignment_id"], claim["assignment_sha256"])
    repo_facts = _make_mock_repo_facts(source_head, scope)

    with pytest.raises(FinalAcceptanceBridgeError, match="distinct"):
        publish_validated_final_acceptance_lineage_locked(
            root=root,
            original_controller_run_id=orig_id,
            continuation_controller_run_id=orig_id,
            before_repository=repo_facts,
            expected_transition_result=enriched_res,
        )


@pytest.mark.parametrize(
    ("fault_type", "match_err"),
    [
        ("dirty_repo", "dirty working directory"),
        ("ref_drift", "protected_refs"),
        ("assignment_scope_drift", "AssignmentScope"),
        ("head_before_drift", "head_before"),
        ("head_after_drift", "head_after"),
        ("inter_check_drift", "drift detected between upstream"),
    ],
)
def test_bridge_pre_publication_drift_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    fault_type: str,
    match_err: str,
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
    before_repo = _make_mock_repo_facts(source_head, scope)
    current_repo = copy.deepcopy(before_repo)

    validator_side_effects: list[Any] = [current_repo, current_repo]

    if fault_type == "dirty_repo":
        validator_side_effects = [RuntimeError("dirty working directory")]
    elif fault_type == "ref_drift":
        bad_refs = copy.deepcopy(_SAMPLE_PROTECTED_REFS)
        bad_refs["refs/heads/main"] = "9" * 40
        current_repo["protected_refs"] = bad_refs
        validator_side_effects = [current_repo, current_repo]
    elif fault_type == "assignment_scope_drift":
        current_repo["assignment_scope"] = _make_sample_scope(
            claim["assignment_id"],
            claim["assignment_sha256"],
            stage_id="drifted-stage-id",
        )
        validator_side_effects = [current_repo, current_repo]
    elif fault_type == "head_before_drift":
        before_repo["head"] = "0" * 40
    elif fault_type == "head_after_drift":
        current_repo["head"] = "0" * 40
        validator_side_effects = [current_repo, current_repo]
    elif fault_type == "inter_check_drift":
        drifted = copy.deepcopy(current_repo)
        drifted["head"] = "8" * 40
        validator_side_effects = [current_repo, drifted]

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_bridge.validate_repository",
        side_effect=validator_side_effects,
    ):
        with mock.patch(
            "scripts.two_site_quota_final_acceptance_bridge.publish_accepted_lineage_record_candidate",
            side_effect=AssertionError("publisher must never be called on pre-publication failure"),
        ) as spy_pub:
            with exclusive_supervisor_lock(data["lock_path"]):
                with pytest.raises(FinalAcceptanceBridgeError, match=match_err):
                    publish_validated_final_acceptance_lineage_locked(
                        root=root,
                        original_controller_run_id=orig_id,
                        continuation_controller_run_id=cont_id,
                        before_repository=before_repo,
                        expected_transition_result=enriched_res,
                    )
            assert spy_pub.call_count == 0


def test_bridge_assignment_scope_and_head_alias_rejected(
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

    # Dict scope alias rejected
    bad_repo_dict_scope = {
        "head": source_head,
        "status": "clean",
        "protected_refs": copy.deepcopy(_SAMPLE_PROTECTED_REFS),
        "assignment_scope": {
            "assignment_id": claim["assignment_id"],
            "sha256": claim["assignment_sha256"],
        },
    }
    with pytest.raises(FinalAcceptanceBridgeError, match="AssignmentScope instance"):
        publish_validated_final_acceptance_lineage_locked(
            root=root,
            original_controller_run_id=orig_id,
            continuation_controller_run_id=cont_id,
            before_repository=bad_repo_dict_scope,
            expected_transition_result=enriched_res,
        )

    # Missing head key rejected (no current_head fallback)
    bad_repo_missing_head = {
        "current_head": source_head,
        "status": "clean",
        "protected_refs": copy.deepcopy(_SAMPLE_PROTECTED_REFS),
        "assignment_scope": scope,
    }
    with pytest.raises(FinalAcceptanceBridgeError, match="missing required 'head' key"):
        publish_validated_final_acceptance_lineage_locked(
            root=root,
            original_controller_run_id=orig_id,
            continuation_controller_run_id=cont_id,
            before_repository=bad_repo_missing_head,
            expected_transition_result=enriched_res,
        )


def test_bridge_missing_or_corrupt_upstream_never_calls_publisher(
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

    data["snapshot_file"].write_text("{\"corrupt\": true}", encoding="utf-8")

    scope = _make_sample_scope(claim["assignment_id"], claim["assignment_sha256"])
    repo_facts = _make_mock_repo_facts(source_head, scope)

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_bridge.validate_repository",
        return_value=repo_facts,
    ):
        with mock.patch(
            "scripts.two_site_quota_final_acceptance_bridge.publish_accepted_lineage_record_candidate",
            side_effect=AssertionError("publisher must never be called on corrupt upstream"),
        ) as spy_pub:
            with exclusive_supervisor_lock(data["lock_path"]):
                with pytest.raises(FinalAcceptanceBridgeError, match="upstream lineage"):
                    publish_validated_final_acceptance_lineage_locked(
                        root=root,
                        original_controller_run_id=orig_id,
                        continuation_controller_run_id=cont_id,
                        before_repository=repo_facts,
                        expected_transition_result=enriched_res,
                    )
            assert spy_pub.call_count == 0


def test_bridge_publisher_failure_is_ambiguous_and_retains_record(
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
    simulated_payload = b'{"simulated": "retained_record_bytes"}'

    def _simulated_publisher_crash(*args: Any, **kwargs: Any) -> Any:
        with _open_existing_cycle_run_directory(root, cont_id) as (run_path, _, _):
            target_path = run_path / ACCEPTED_LINEAGE_FILENAME
            target_path.write_bytes(simulated_payload)
            os.chmod(target_path, 0o600)
        raise RuntimeError("simulated IO error after linking target")

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_bridge.validate_repository",
        return_value=repo_facts,
    ):
        with mock.patch(
            "scripts.two_site_quota_final_acceptance_bridge.publish_accepted_lineage_record_candidate",
            side_effect=_simulated_publisher_crash,
        ):
            with exclusive_supervisor_lock(data["lock_path"]):
                with pytest.raises(
                    FinalAcceptanceBridgeAmbiguousPostPublicationError,
                    match="ambiguous post-publication failure",
                ) as exc_info:
                    publish_validated_final_acceptance_lineage_locked(
                        root=root,
                        original_controller_run_id=orig_id,
                        continuation_controller_run_id=cont_id,
                        before_repository=repo_facts,
                        expected_transition_result=enriched_res,
                    )
                assert "simulated IO error" in str(exc_info.value)

    with _open_existing_cycle_run_directory(root, cont_id) as (run_path, _, _):
        target_path = run_path / ACCEPTED_LINEAGE_FILENAME
        assert target_path.is_file()
        assert target_path.read_bytes() == simulated_payload


def test_bridge_duplicate_publication_fails_closed(
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

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_bridge.validate_repository",
        return_value=repo_facts,
    ):
        with exclusive_supervisor_lock(data["lock_path"]):
            publish_validated_final_acceptance_lineage_locked(
                root=root,
                original_controller_run_id=orig_id,
                continuation_controller_run_id=cont_id,
                before_repository=repo_facts,
                expected_transition_result=enriched_res,
            )

            with pytest.raises(
                FinalAcceptanceBridgeAmbiguousPostPublicationError,
                match="already exists",
            ):
                publish_validated_final_acceptance_lineage_locked(
                    root=root,
                    original_controller_run_id=orig_id,
                    continuation_controller_run_id=cont_id,
                    before_repository=repo_facts,
                    expected_transition_result=enriched_res,
                )


def test_bridge_ambiguous_post_publication_drift_retains_record(
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
    drifted_repo_facts = copy.deepcopy(repo_facts)
    drifted_repo_facts["head"] = "7" * 40

    with mock.patch(
        "scripts.two_site_quota_final_acceptance_bridge.validate_repository",
        side_effect=[repo_facts, repo_facts, drifted_repo_facts],
    ):
        with exclusive_supervisor_lock(data["lock_path"]):
            with pytest.raises(
                FinalAcceptanceBridgeAmbiguousPostPublicationError,
                match="ambiguous post-publication failure",
            ):
                publish_validated_final_acceptance_lineage_locked(
                    root=root,
                    original_controller_run_id=orig_id,
                    continuation_controller_run_id=cont_id,
                    before_repository=repo_facts,
                    expected_transition_result=enriched_res,
                )

    with _open_existing_cycle_run_directory(root, cont_id) as (run_path, _, _):
        target_file = run_path / ACCEPTED_LINEAGE_FILENAME
        assert target_file.is_file(), "published record must be retained on post-publication drift"
