#!/usr/bin/env python3
"""Offline unit tests for retry classification of malformed provider JSON responses."""
from __future__ import annotations

import json
import pytest

try:
    from scripts.two_site_proposal_runner import (
        MODEL,
        ProposalError,
        RetryableProviderSessionError,
        parse_stream,
    )
except ModuleNotFoundError:
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        MODEL,
        ProposalError,
        RetryableProviderSessionError,
        parse_stream,
    )


def make_task_and_sources() -> tuple[dict, dict]:
    task = {
        "task_id": "automation-retry-malformed-provider-json-small-20260912",
        "goal": "Verify malformed provider json response classification.",
        "context_files": [],
        "allowed_files": [],
        "requirements": ["Requirement 1"],
        "test_selectors": ["pytest"],
    }
    sources: dict = {}
    return task, sources


def make_valid_proposal() -> dict:
    return {
        "status": "VERIFIED",
        "summary": "Verified proposal payload.",
        "covered_requirements": [1],
        "findings": [],
        "changes": [],
    }


def make_stream_raw(
    *,
    response: str | None = None,
    structured_output: dict | None = None,
    session_id: str = "test-session-001",
) -> str:
    init_event = {
        "event": "init",
        "conversation_id": session_id,
        "init": {"model": MODEL},
    }
    result_envelope: dict = {
        "conversation_id": session_id,
        "status": "SUCCESS",
    }
    if response is not None:
        result_envelope["response"] = response
    if structured_output is not None:
        result_envelope["structured_output"] = structured_output
    result_event = {
        "event": "result",
        "conversation_id": session_id,
        "result": result_envelope,
    }
    return f"{json.dumps(init_event)}\n{json.dumps(result_event)}\n"


def test_empty_response_raises_retryable_provider_session_error():
    task, sources = make_task_and_sources()
    raw = make_stream_raw(response="")
    with pytest.raises(RetryableProviderSessionError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert type(exc_info.value) is RetryableProviderSessionError
    assert str(exc_info.value) == "CLI response is not one valid JSON document"
    assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)


def test_truncated_response_raises_retryable_provider_session_error():
    task, sources = make_task_and_sources()
    raw = make_stream_raw(response='{"status": "VERIFIED", "summary": "incomplete')
    with pytest.raises(RetryableProviderSessionError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert type(exc_info.value) is RetryableProviderSessionError
    assert str(exc_info.value) == "CLI response is not one valid JSON document"
    assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)


def test_concatenated_multiple_response_raises_retryable_provider_session_error():
    task, sources = make_task_and_sources()
    proposal = make_valid_proposal()
    multi_json = f"{json.dumps(proposal)}\n{json.dumps(proposal)}"
    raw = make_stream_raw(response=multi_json)
    with pytest.raises(RetryableProviderSessionError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert type(exc_info.value) is RetryableProviderSessionError
    assert str(exc_info.value) == "CLI response is not one valid JSON document"
    assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)


def test_valid_single_response_passes():
    task, sources = make_task_and_sources()
    proposal = make_valid_proposal()
    raw = make_stream_raw(response=json.dumps(proposal))
    result, envelope = parse_stream(raw, task, sources, "verifier")
    assert result == proposal
    assert envelope["status"] == "SUCCESS"
    assert envelope["conversation_id"] == "test-session-001"


def test_duplicate_key_response_raises_proposal_error_not_retryable():
    task, sources = make_task_and_sources()
    duplicate_key_json = (
        '{"status": "VERIFIED", "summary": "first", "summary": "second", '
        '"covered_requirements": [1], "findings": [], "changes": []}'
    )
    raw = make_stream_raw(response=duplicate_key_json)
    with pytest.raises(ProposalError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert not isinstance(exc_info.value, RetryableProviderSessionError)
    assert "duplicate JSON key" in str(exc_info.value)


def test_invalid_structured_output_raises_proposal_error_not_retryable():
    task, sources = make_task_and_sources()
    raw = make_stream_raw(structured_output={"unexpected_key": "bad_data"})
    with pytest.raises(ProposalError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert not isinstance(exc_info.value, RetryableProviderSessionError)


def test_invalid_structured_output_with_malformed_response_raises_proposal_error():
    task, sources = make_task_and_sources()
    raw = make_stream_raw(
        response="malformed { json",
        structured_output={"status": "INVALID_STATUS"},
    )
    with pytest.raises(ProposalError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert not isinstance(exc_info.value, RetryableProviderSessionError)
