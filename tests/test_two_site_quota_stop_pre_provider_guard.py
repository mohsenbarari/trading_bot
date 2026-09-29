"""Hermetic tests for pre-provider STOP guard in quota continuation route adapter."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts import two_site_provider_broker as broker_mod
from scripts.two_site_provider_broker import BrokerError, validate_response
from scripts.two_site_quota_broker_route import execute_intent_bound_continuation
import scripts.two_site_quota_broker_route as route_mod
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_continuation_broker_recovery import (
    _assert_runs_unchanged,
    _setup_recovery_test_case,
    _terminal_file,
)


@pytest.mark.parametrize("stop_kind", ["regular_file", "broken_symlink"])
def test_pre_dispatch_stop_file_blocks_provider(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    stop_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    resume_request = data["resume_request"]
    req_id = data["req_id"]
    claim_path = data["claim_path"]
    response_path = data["response_path"]
    claim_bytes_before = claim_path.read_bytes()

    dispatcher_spy = MagicMock()
    monkeypatch.setattr(
        "scripts.two_site_quota_broker_route.dispatch_quota_continuation_once",
        dispatcher_spy,
    )

    stop_path = root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
    orig_read = route_mod.read_quota_resume_submission_intent

    def read_then_inject_stop(*args: Any, **kwargs: Any) -> Any:
        result = orig_read(*args, **kwargs)
        stop_path.parent.mkdir(parents=True, exist_ok=True)
        if stop_kind == "regular_file":
            stop_path.write_text("STOP\n", encoding="utf-8")
        else:
            target = root / "nonexistent_target_broken_symlink"
            stop_path.symlink_to(target)
        return result

    monkeypatch.setattr(
        "scripts.two_site_quota_broker_route.read_quota_resume_submission_intent",
        read_then_inject_stop,
    )

    with pytest.raises(
        BrokerError, match="stop requested before continuation dispatch"
    ) as exc_info:
        execute_intent_bound_continuation(root, resume_request)

    assert str(exc_info.value) == "stop requested before continuation dispatch"
    dispatcher_spy.assert_not_called()
    assert claim_path.is_file()
    assert claim_path.read_bytes() == claim_bytes_before
    _assert_runs_unchanged(data)
    assert not response_path.exists()
    assert not _terminal_file(root, req_id).exists()


def test_pre_dispatch_stop_signal_flag_blocks_provider(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    resume_request = data["resume_request"]
    req_id = data["req_id"]
    claim_path = data["claim_path"]
    response_path = data["response_path"]
    claim_bytes_before = claim_path.read_bytes()

    dispatcher_spy = MagicMock()
    monkeypatch.setattr(
        "scripts.two_site_quota_broker_route.dispatch_quota_continuation_once",
        dispatcher_spy,
    )

    orig_read = route_mod.read_quota_resume_submission_intent

    def read_then_inject_signal(*args: Any, **kwargs: Any) -> Any:
        result = orig_read(*args, **kwargs)
        monkeypatch.setattr(broker_mod, "_STOP", True)
        return result

    monkeypatch.setattr(
        "scripts.two_site_quota_broker_route.read_quota_resume_submission_intent",
        read_then_inject_signal,
    )

    with pytest.raises(
        BrokerError, match="stop requested before continuation dispatch"
    ) as exc_info:
        execute_intent_bound_continuation(root, resume_request)

    assert str(exc_info.value) == "stop requested before continuation dispatch"
    dispatcher_spy.assert_not_called()
    assert claim_path.is_file()
    assert claim_path.read_bytes() == claim_bytes_before
    _assert_runs_unchanged(data)
    assert not response_path.exists()
    assert not _terminal_file(root, req_id).exists()


@pytest.mark.parametrize(
    "bad_state", ["missing", "invalid_type_int", "invalid_type_str", "invalid_type_none"]
)
def test_pre_dispatch_missing_or_malformed_stop_fails_closed(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    bad_state: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    resume_request = data["resume_request"]
    req_id = data["req_id"]
    claim_path = data["claim_path"]
    response_path = data["response_path"]
    claim_bytes_before = claim_path.read_bytes()

    dispatcher_spy = MagicMock()
    monkeypatch.setattr(
        "scripts.two_site_quota_broker_route.dispatch_quota_continuation_once",
        dispatcher_spy,
    )

    orig_read = route_mod.read_quota_resume_submission_intent

    def read_then_corrupt_stop(*args: Any, **kwargs: Any) -> Any:
        result = orig_read(*args, **kwargs)
        if bad_state == "missing":
            monkeypatch.delattr(broker_mod, "_STOP", raising=False)
        elif bad_state == "invalid_type_int":
            monkeypatch.setattr(broker_mod, "_STOP", 1)
        elif bad_state == "invalid_type_str":
            monkeypatch.setattr(broker_mod, "_STOP", "invalid")
        else:
            monkeypatch.setattr(broker_mod, "_STOP", None)
        return result

    monkeypatch.setattr(
        "scripts.two_site_quota_broker_route.read_quota_resume_submission_intent",
        read_then_corrupt_stop,
    )

    with pytest.raises(
        BrokerError, match="stop requested before continuation dispatch"
    ) as exc_info:
        execute_intent_bound_continuation(root, resume_request)

    assert str(exc_info.value) == "stop requested before continuation dispatch"
    dispatcher_spy.assert_not_called()
    assert claim_path.is_file()
    assert claim_path.read_bytes() == claim_bytes_before
    _assert_runs_unchanged(data)
    assert not response_path.exists()
    assert not _terminal_file(root, req_id).exists()


def test_no_stop_allows_single_dispatch_and_valid_response(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    resume_request = data["resume_request"]
    stub_result = {
        "state": "READY_FOR_CODEX",
        "summary": "Hermetic dispatch success",
    }

    dispatcher_spy = MagicMock(return_value=stub_result)
    monkeypatch.setattr(
        "scripts.two_site_quota_broker_route.dispatch_quota_continuation_once",
        dispatcher_spy,
    )

    response = execute_intent_bound_continuation(root, resume_request)

    dispatcher_spy.assert_called_once()
    assert dispatcher_spy.call_args[0][0] == root
    assert dispatcher_spy.call_args[0][1]["request_id"] == resume_request["request_id"]

    assert response["schema_version"] == "1.0"
    assert response["request_id"] == resume_request["request_id"]
    assert response["run_prefix"] == resume_request["run_prefix"]
    assert response["source_head"] == resume_request["source_head"]
    assert response["state"] == "COMPLETED"
    assert response["exit_code"] == 0
    assert response["stderr"] == ""
    assert json.loads(response["stdout"]) == stub_result
    assert validate_response(resume_request, response) == response


def test_stop_inside_entered_dispatch_does_not_duplicate_or_erase(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves one in-flight call and completed response object when STOP arrives inside dispatch.

    Note: durable Broker terminal-receipt custody is covered by broker recovery tests.
    """
    data = _setup_recovery_test_case(broker_repo)
    root = data["root"]
    resume_request = data["resume_request"]
    stub_result = {
        "state": "READY_FOR_CODEX",
        "summary": "Completed in-flight continuation",
    }

    stop_path = root / ".local" / "two-site-refactor" / "autopilot" / "STOP"

    def dispatch_with_stop_arrival(r: Path, adm: dict[str, Any]) -> dict[str, Any]:
        stop_path.parent.mkdir(parents=True, exist_ok=True)
        stop_path.write_text("STOP\n", encoding="utf-8")
        monkeypatch.setattr(broker_mod, "_STOP", True)
        return stub_result

    dispatcher_spy = MagicMock(side_effect=dispatch_with_stop_arrival)
    monkeypatch.setattr(
        "scripts.two_site_quota_broker_route.dispatch_quota_continuation_once",
        dispatcher_spy,
    )

    response = execute_intent_bound_continuation(root, resume_request)

    dispatcher_spy.assert_called_once()
    assert response["schema_version"] == "1.0"
    assert response["request_id"] == resume_request["request_id"]
    assert response["state"] == "COMPLETED"
    assert json.loads(response["stdout"]) == stub_result
    assert validate_response(resume_request, response) == response
    assert stop_path.is_file()
