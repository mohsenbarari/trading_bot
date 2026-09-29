"""Fault and ambiguity characterization tests for final acknowledgement bridge."""

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
import tempfile
from typing import Any
from unittest.mock import patch

import pytest

import scripts.two_site_quota_final_ack_storage
from scripts.two_site_quota_final_ack_bridge import (
    FinalAckBridgeAmbiguousPostPublicationError,
    publish_validated_final_ack_locked,
)
from scripts.two_site_quota_final_ack_storage import (
    FINAL_ACK_FILENAME,
    FinalAckStorageAmbiguousPostPublicationError,
    FinalAckStorageError,
    read_final_ack_record_candidate,
)
from tests.test_two_site_quota_final_ack_bridge_basic import _setup_bridge_env


def _assert_retained_final_ack(
    root: Path,
    orig_id: str,
    cont_id: str,
    exp: dict[str, Any],
    ctx: dict[str, Any],
    up: dict[str, Any],
    cycle_dir: Path,
    exc_info: pytest.ExceptionInfo[Any],
    marker: str,
    pub_calls: int,
) -> None:
    assert exc_info.type is FinalAckBridgeAmbiguousPostPublicationError
    assert marker not in str(exc_info.value)
    assert pub_calls == 1

    target = cycle_dir / FINAL_ACK_FILENAME
    assert target.is_file()
    st = os.stat(target)
    assert stat.S_ISREG(st.st_mode)
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert st.st_nlink == 1

    first_bytes = target.read_bytes()
    data = json.loads(first_bytes.decode("utf-8"))
    assert data["schema_version"] == "1.0"
    assert data["original_controller_run_id"] == orig_id
    assert data["continuation_controller_run_id"] == cont_id

    readback = read_final_ack_record_candidate(
        root=root,
        original_controller_run_id=orig_id,
        continuation_controller_run_id=cont_id,
        expected_transition_result=exp,
        acceptance_context=ctx,
        upstream=up,
    )
    assert readback["accepted_state"] == exp["state"]

    assert [f for f in os.listdir(cycle_dir) if f.startswith(".")] == []

    with pytest.raises(FinalAckStorageError, match="already exists"):
        scripts.two_site_quota_final_ack_storage.publish_final_ack_record_candidate(
            root=root,
            original_controller_run_id=orig_id,
            continuation_controller_run_id=cont_id,
            expected_transition_result=exp,
            acceptance_context=ctx,
            upstream=up,
            acknowledged_at_utc="2026-09-29T12:06:00Z",
        )
    assert target.read_bytes() == first_bytes
    st_after = os.stat(target)
    assert st_after.st_ino == st.st_ino
    assert st_after.st_mtime_ns == st.st_mtime_ns


@pytest.mark.parametrize(
    ("fault", "marker"),
    [
        (
            FinalAckStorageAmbiguousPostPublicationError(
                "injected_explicit_storage_ambiguity_fault"
            ),
            "injected_explicit_storage_ambiguity_fault",
        ),
        (
            KeyboardInterrupt("injected_pub_keyboard_interrupt_fault"),
            "injected_pub_keyboard_interrupt_fault",
        ),
        (
            SystemExit("injected_pub_system_exit_fault"),
            "injected_pub_system_exit_fault",
        ),
    ],
)
def test_publication_seam_faults_map_to_ambiguity_and_retained(
    fault: BaseException, marker: str
) -> None:
    with tempfile.TemporaryDirectory() as td:
        root, orig_id, cont_id, exp, ctx, up, cycle_dir, lineage, repo_facts = (
            _setup_bridge_env(td, complete=False)
        )
        real_pub = (
            scripts.two_site_quota_final_ack_storage.publish_final_ack_record_candidate
        )
        pub_calls = 0

        def _publish_wrapper(*args: Any, **kwargs: Any) -> Any:
            nonlocal pub_calls
            pub_calls += 1
            real_pub(*args, **kwargs)
            raise fault

        with patch(
            "scripts.two_site_quota_final_ack_bridge.validate_repository",
            return_value=repo_facts,
        ), patch(
            "scripts.two_site_quota_final_ack_bridge.publish_final_ack_record_candidate",
            side_effect=_publish_wrapper,
        ):
            with pytest.raises(
                FinalAckBridgeAmbiguousPostPublicationError
            ) as exc_info:
                publish_validated_final_ack_locked(
                    root=root,
                    original_controller_run_id=orig_id,
                    continuation_controller_run_id=cont_id,
                    expected_transition_result=exp,
                    accepted_lineage_record=lineage,
                    acknowledged_at_utc="2026-09-29T12:05:00Z",
                )

        _assert_retained_final_ack(
            root=root,
            orig_id=orig_id,
            cont_id=cont_id,
            exp=exp,
            ctx=ctx,
            up=up,
            cycle_dir=cycle_dir,
            exc_info=exc_info,
            marker=marker,
            pub_calls=pub_calls,
        )


@pytest.mark.parametrize(
    ("fault", "marker"),
    [
        (
            {"head_drift": True},
            "injected_repo_drift_marker",
        ),
        (
            KeyboardInterrupt("injected_repo_keyboard_interrupt_fault"),
            "injected_repo_keyboard_interrupt_fault",
        ),
        (
            SystemExit("injected_repo_system_exit_fault"),
            "injected_repo_system_exit_fault",
        ),
    ],
)
def test_post_publication_repo_drift_and_interrupts_map_to_ambiguity_and_retained(
    fault: Any, marker: str
) -> None:
    with tempfile.TemporaryDirectory() as td:
        root, orig_id, cont_id, exp, ctx, up, cycle_dir, lineage, repo_facts = (
            _setup_bridge_env(td, complete=False)
        )
        real_pub = (
            scripts.two_site_quota_final_ack_storage.publish_final_ack_record_candidate
        )
        pub_calls = 0

        def _spy_pub(*args: Any, **kwargs: Any) -> Any:
            nonlocal pub_calls
            pub_calls += 1
            return real_pub(*args, **kwargs)

        val_calls = 0

        def _mock_validate(*args: Any, **kwargs: Any) -> Any:
            nonlocal val_calls
            val_calls += 1
            if val_calls == 1:
                return repo_facts
            if isinstance(fault, BaseException):
                raise fault
            drifted = copy.deepcopy(repo_facts)
            drifted["head"] = "0" * 40
            return drifted

        with patch(
            "scripts.two_site_quota_final_ack_bridge.validate_repository",
            side_effect=_mock_validate,
        ), patch(
            "scripts.two_site_quota_final_ack_bridge.publish_final_ack_record_candidate",
            side_effect=_spy_pub,
        ):
            with pytest.raises(
                FinalAckBridgeAmbiguousPostPublicationError
            ) as exc_info:
                publish_validated_final_ack_locked(
                    root=root,
                    original_controller_run_id=orig_id,
                    continuation_controller_run_id=cont_id,
                    expected_transition_result=exp,
                    accepted_lineage_record=lineage,
                    acknowledged_at_utc="2026-09-29T12:05:00Z",
                )

        assert val_calls == 2
        _assert_retained_final_ack(
            root=root,
            orig_id=orig_id,
            cont_id=cont_id,
            exp=exp,
            ctx=ctx,
            up=up,
            cycle_dir=cycle_dir,
            exc_info=exc_info,
            marker=marker,
            pub_calls=pub_calls,
        )


@pytest.mark.parametrize(
    ("fault", "marker"),
    [
        (
            FinalAckStorageError("injected_readback_storage_fault"),
            "injected_readback_storage_fault",
        ),
        (
            KeyboardInterrupt("injected_readback_keyboard_interrupt_fault"),
            "injected_readback_keyboard_interrupt_fault",
        ),
        (
            SystemExit("injected_readback_system_exit_fault"),
            "injected_readback_system_exit_fault",
        ),
        (
            {"schema_version": "9.9", "mismatch_payload": "injected_mismatch"},
            "injected_mismatch",
        ),
    ],
)
def test_post_publication_readback_faults_map_to_ambiguity_and_retained(
    fault: Any, marker: str
) -> None:
    with tempfile.TemporaryDirectory() as td:
        root, orig_id, cont_id, exp, ctx, up, cycle_dir, lineage, repo_facts = (
            _setup_bridge_env(td, complete=False)
        )
        real_pub = (
            scripts.two_site_quota_final_ack_storage.publish_final_ack_record_candidate
        )
        pub_calls = 0

        def _spy_pub(*args: Any, **kwargs: Any) -> Any:
            nonlocal pub_calls
            pub_calls += 1
            return real_pub(*args, **kwargs)

        def _mock_readback(*args: Any, **kwargs: Any) -> Any:
            if isinstance(fault, BaseException):
                raise fault
            return fault

        with patch(
            "scripts.two_site_quota_final_ack_bridge.validate_repository",
            return_value=repo_facts,
        ), patch(
            "scripts.two_site_quota_final_ack_bridge.publish_final_ack_record_candidate",
            side_effect=_spy_pub,
        ), patch(
            "scripts.two_site_quota_final_ack_bridge.read_final_ack_record_candidate",
            side_effect=_mock_readback,
        ):
            with pytest.raises(
                FinalAckBridgeAmbiguousPostPublicationError
            ) as exc_info:
                publish_validated_final_ack_locked(
                    root=root,
                    original_controller_run_id=orig_id,
                    continuation_controller_run_id=cont_id,
                    expected_transition_result=exp,
                    accepted_lineage_record=lineage,
                    acknowledged_at_utc="2026-09-29T12:05:00Z",
                )

        _assert_retained_final_ack(
            root=root,
            orig_id=orig_id,
            cont_id=cont_id,
            exp=exp,
            ctx=ctx,
            up=up,
            cycle_dir=cycle_dir,
            exc_info=exc_info,
            marker=marker,
            pub_calls=pub_calls,
        )
