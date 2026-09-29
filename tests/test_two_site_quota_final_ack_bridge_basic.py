"""Basic unit characterization tests for final acknowledgement bridge."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import copy
from pathlib import Path
import stat
import tempfile
from typing import Any
from unittest.mock import patch

import pytest

from scripts.two_site_quota_accepted_lineage_storage import (
    read_accepted_lineage_record_candidate,
)
from scripts.two_site_quota_final_ack_bridge import (
    FinalAckBridgeError,
    publish_validated_final_ack_locked,
)
from scripts.two_site_quota_final_ack_storage import (
    FINAL_ACK_FILENAME,
    read_final_ack_record_candidate,
)
from tests.test_two_site_quota_final_acceptance_bridge import (
    _make_mock_repo_facts,
    _make_sample_scope,
)
from tests.test_two_site_quota_final_ack_storage import _init_env


def _setup_bridge_env(
    td: str, complete: bool = False
) -> tuple[
    Path,
    str,
    str,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    Path,
    dict[str, Any],
    dict[str, Any],
]:
    root, orig_id, cont_id, exp, ctx, up, cycle_dir = _init_env(td, complete=complete)
    lineage = read_accepted_lineage_record_candidate(
        root=root,
        continuation_controller_run_id=cont_id,
        expected_transition_result=exp,
        acceptance_context=ctx,
        upstream=up,
    )
    scope = _make_sample_scope(ctx["assignment_id"], ctx["assignment_sha256"])
    repo_facts = _make_mock_repo_facts(
        exp["head_after"], scope, protected_refs=ctx["protected_refs"]
    )
    return root, orig_id, cont_id, exp, ctx, up, cycle_dir, lineage, repo_facts


def test_bridge_positive_continue() -> None:
    ack_time = "2026-09-29T12:05:00Z"
    with tempfile.TemporaryDirectory() as td:
        root, orig_id, cont_id, exp, ctx, up, cycle_dir, lineage, repo_facts = (
            _setup_bridge_env(td, complete=False)
        )
        with patch(
            "scripts.two_site_quota_final_ack_bridge.validate_repository",
            return_value=repo_facts,
        ) as mock_val:
            pub = publish_validated_final_ack_locked(
                root=root,
                original_controller_run_id=orig_id,
                continuation_controller_run_id=cont_id,
                expected_transition_result=exp,
                accepted_lineage_record=lineage,
                acknowledged_at_utc=ack_time,
            )

        assert mock_val.call_count == 2
        assert pub["accepted_state"] == "CONTINUE"
        assert pub["original_controller_run_id"] == orig_id
        assert pub["continuation_controller_run_id"] == cont_id
        assert pub["acknowledged_at_utc"] == ack_time

        target = cycle_dir / FINAL_ACK_FILENAME
        assert target.is_file()
        st = os.stat(target)
        assert stat.S_ISREG(st.st_mode)
        assert stat.S_IMODE(st.st_mode) == 0o600
        assert st.st_nlink == 1

        readback = read_final_ack_record_candidate(
            root=root,
            original_controller_run_id=orig_id,
            continuation_controller_run_id=cont_id,
            expected_transition_result=exp,
            acceptance_context=ctx,
            upstream=up,
        )
        assert readback == pub
        assert [f for f in os.listdir(cycle_dir) if f.startswith(".")] == []


def test_bridge_positive_complete() -> None:
    ack_time = "2026-09-29T12:05:00Z"
    with tempfile.TemporaryDirectory() as td:
        root, orig_id, cont_id, exp, ctx, up, cycle_dir, lineage, repo_facts = (
            _setup_bridge_env(td, complete=True)
        )
        with patch(
            "scripts.two_site_quota_final_ack_bridge.validate_repository",
            return_value=repo_facts,
        ) as mock_val:
            pub = publish_validated_final_ack_locked(
                root=root,
                original_controller_run_id=orig_id,
                continuation_controller_run_id=cont_id,
                expected_transition_result=exp,
                accepted_lineage_record=lineage,
                acknowledged_at_utc=ack_time,
            )

        assert mock_val.call_count == 2
        assert pub["accepted_state"] == "COMPLETE"
        assert pub["head_after"] == exp["head_before"]

        target = cycle_dir / FINAL_ACK_FILENAME
        assert target.is_file()
        st = os.stat(target)
        assert stat.S_ISREG(st.st_mode)
        assert stat.S_IMODE(st.st_mode) == 0o600
        assert st.st_nlink == 1

        readback = read_final_ack_record_candidate(
            root=root,
            original_controller_run_id=orig_id,
            continuation_controller_run_id=cont_id,
            expected_transition_result=exp,
            acceptance_context=ctx,
            upstream=up,
        )
        assert readback == pub
        assert [f for f in os.listdir(cycle_dir) if f.startswith(".")] == []


def test_bridge_supplied_lineage_mismatch_fails_pre_publication() -> None:
    with tempfile.TemporaryDirectory() as td:
        root, orig_id, cont_id, exp, ctx, up, cycle_dir, lineage, repo_facts = (
            _setup_bridge_env(td, complete=False)
        )
        mismatched_lineage = copy.deepcopy(lineage)
        mismatched_lineage["accepted_state"] = "COMPLETE"

        with patch(
            "scripts.two_site_quota_final_ack_bridge.publish_final_ack_record_candidate"
        ) as mock_pub:
            with pytest.raises(
                FinalAckBridgeError,
                match="supplied accepted_lineage_record does not match persisted accepted lineage record",
            ):
                publish_validated_final_ack_locked(
                    root=root,
                    original_controller_run_id=orig_id,
                    continuation_controller_run_id=cont_id,
                    expected_transition_result=exp,
                    accepted_lineage_record=mismatched_lineage,
                    acknowledged_at_utc="2026-09-29T12:05:00Z",
                )
            mock_pub.assert_not_called()

        assert not (cycle_dir / FINAL_ACK_FILENAME).exists()


@pytest.mark.parametrize(
    ("drift_type", "expected_err"),
    [
        ("head_drift", "current clean HEAD does not match result head_after"),
        (
            "ref_drift",
            "repository protected_refs does not match persisted lineage protected_refs",
        ),
        (
            "assignment_id_drift",
            "repository assignment_id does not match persisted lineage assignment_id",
        ),
        (
            "assignment_sha_drift",
            "repository assignment_sha256 does not match persisted lineage assignment_sha256",
        ),
    ],
)
def test_bridge_pre_publication_repo_drift_fails_closed(
    drift_type: str, expected_err: str
) -> None:
    with tempfile.TemporaryDirectory() as td:
        root, orig_id, cont_id, exp, ctx, up, cycle_dir, lineage, repo_facts = (
            _setup_bridge_env(td, complete=False)
        )
        drifted_facts = copy.deepcopy(repo_facts)
        if drift_type == "head_drift":
            drifted_facts["head"] = "0" * 40
        elif drift_type == "ref_drift":
            drifted_facts["protected_refs"]["refs/heads/main"] = "0" * 40
        elif drift_type == "assignment_id_drift":
            drifted_facts["assignment_scope"] = _make_sample_scope(
                "drifted-assignment-id",
                ctx["assignment_sha256"],
            )
        elif drift_type == "assignment_sha_drift":
            drifted_facts["assignment_scope"] = _make_sample_scope(
                ctx["assignment_id"],
                "0" * 64,
            )

        with patch(
            "scripts.two_site_quota_final_ack_bridge.validate_repository",
            return_value=drifted_facts,
        ), patch(
            "scripts.two_site_quota_final_ack_bridge.publish_final_ack_record_candidate"
        ) as mock_pub:
            with pytest.raises(FinalAckBridgeError, match=expected_err):
                publish_validated_final_ack_locked(
                    root=root,
                    original_controller_run_id=orig_id,
                    continuation_controller_run_id=cont_id,
                    expected_transition_result=exp,
                    accepted_lineage_record=lineage,
                    acknowledged_at_utc="2026-09-29T12:05:00Z",
                )
            mock_pub.assert_not_called()

        assert not (cycle_dir / FINAL_ACK_FILENAME).exists()


def test_bridge_duplicate_publication_fails_closed_without_overwrite() -> None:
    with tempfile.TemporaryDirectory() as td:
        root, orig_id, cont_id, exp, ctx, up, cycle_dir, lineage, repo_facts = (
            _setup_bridge_env(td, complete=False)
        )
        with patch(
            "scripts.two_site_quota_final_ack_bridge.validate_repository",
            return_value=repo_facts,
        ):
            pub1 = publish_validated_final_ack_locked(
                root=root,
                original_controller_run_id=orig_id,
                continuation_controller_run_id=cont_id,
                expected_transition_result=exp,
                accepted_lineage_record=lineage,
                acknowledged_at_utc="2026-09-29T12:05:00Z",
            )
            assert pub1["accepted_state"] == "CONTINUE"

            target = cycle_dir / FINAL_ACK_FILENAME
            assert target.is_file()
            first_bytes = target.read_bytes()
            first_st = os.stat(target)

            with pytest.raises(FinalAckBridgeError) as exc_info:
                publish_validated_final_ack_locked(
                    root=root,
                    original_controller_run_id=orig_id,
                    continuation_controller_run_id=cont_id,
                    expected_transition_result=exp,
                    accepted_lineage_record=lineage,
                    acknowledged_at_utc="2026-09-29T12:06:00Z",
                )
            assert "pre-publication ack storage failure" in str(exc_info.value)
            assert "already exists" in str(exc_info.value.__cause__)

            assert target.read_bytes() == first_bytes
            second_st = os.stat(target)
            assert second_st.st_ino == first_st.st_ino
            assert second_st.st_mtime_ns == first_st.st_mtime_ns
            assert [f for f in os.listdir(cycle_dir) if f.startswith(".")] == []
