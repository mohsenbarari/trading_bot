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


def test_concatenated_identical_multiple_response_accepted():
    task, sources = make_task_and_sources()
    proposal = make_valid_proposal()
    multi_json = f"{json.dumps(proposal)}\n{json.dumps(proposal)}"
    raw = make_stream_raw(response=multi_json)
    result, envelope = parse_stream(raw, task, sources, "verifier")
    assert result == proposal
    assert envelope["status"] == "SUCCESS"
    assert envelope["conversation_id"] == "test-session-001"


def test_concatenated_divergent_multiple_response_raises_retryable_provider_session_error():
    task, sources = make_task_and_sources()
    proposal1 = make_valid_proposal()
    proposal2 = dict(proposal1)
    proposal2["summary"] = "divergent summary"
    multi_json = f"{json.dumps(proposal1)}\n{json.dumps(proposal2)}"
    raw = make_stream_raw(response=multi_json)
    with pytest.raises(RetryableProviderSessionError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert type(exc_info.value) is RetryableProviderSessionError
    assert str(exc_info.value) == "CLI response is not one valid JSON document"


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


def test_two_copies_with_last_copy_decorated_accepted():
    task, sources = make_task_and_sources()
    proposal = make_valid_proposal()
    decorated = dict(proposal, toolAction="Editing file", toolSummary="File edit")
    multi_json = f"{json.dumps(proposal)}\n{json.dumps(decorated)}"
    raw = make_stream_raw(response=multi_json)
    result, envelope = parse_stream(raw, task, sources, "verifier")
    assert result == proposal
    assert "toolAction" not in result
    assert "toolSummary" not in result
    assert envelope["status"] == "SUCCESS"
    assert envelope["conversation_id"] == "test-session-001"


def test_three_copies_with_last_copy_decorated_accepted():
    task, sources = make_task_and_sources()
    proposal = make_valid_proposal()
    decorated = dict(proposal, toolAction="Running command", toolSummary="Command execution")
    multi_json = f"{json.dumps(proposal)}\n{json.dumps(proposal)}\n{json.dumps(decorated)}"
    raw = make_stream_raw(response=multi_json)
    result, envelope = parse_stream(raw, task, sources, "verifier")
    assert result == proposal
    assert "toolAction" not in result
    assert "toolSummary" not in result
    assert envelope["status"] == "SUCCESS"


def test_decoration_on_first_copy_raises_retryable_provider_session_error():
    task, sources = make_task_and_sources()
    proposal = make_valid_proposal()
    decorated = dict(proposal, toolAction="Editing file", toolSummary="File edit")
    multi_json = f"{json.dumps(decorated)}\n{json.dumps(proposal)}"
    raw = make_stream_raw(response=multi_json)
    with pytest.raises(RetryableProviderSessionError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert type(exc_info.value) is RetryableProviderSessionError
    assert str(exc_info.value) == "CLI response is not one valid JSON document"


def test_only_one_decoration_key_raises_retryable_provider_session_error():
    task, sources = make_task_and_sources()
    proposal = make_valid_proposal()
    decorated1 = dict(proposal, toolAction="Editing file")
    raw1 = make_stream_raw(response=f"{json.dumps(proposal)}\n{json.dumps(decorated1)}")
    with pytest.raises(RetryableProviderSessionError) as exc_info1:
        parse_stream(raw1, task, sources, "verifier")
    assert type(exc_info1.value) is RetryableProviderSessionError
    assert str(exc_info1.value) == "CLI response is not one valid JSON document"

    decorated2 = dict(proposal, toolSummary="File edit")
    raw2 = make_stream_raw(response=f"{json.dumps(proposal)}\n{json.dumps(decorated2)}")
    with pytest.raises(RetryableProviderSessionError) as exc_info2:
        parse_stream(raw2, task, sources, "verifier")
    assert type(exc_info2.value) is RetryableProviderSessionError
    assert str(exc_info2.value) == "CLI response is not one valid JSON document"


def test_unknown_extra_key_raises_retryable_provider_session_error():
    task, sources = make_task_and_sources()
    proposal = make_valid_proposal()
    decorated = dict(
        proposal,
        toolAction="Editing file",
        toolSummary="File edit",
        unknownKey="unexpected",
    )
    raw = make_stream_raw(response=f"{json.dumps(proposal)}\n{json.dumps(decorated)}")
    with pytest.raises(RetryableProviderSessionError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert type(exc_info.value) is RetryableProviderSessionError
    assert str(exc_info.value) == "CLI response is not one valid JSON document"


def test_divergent_decorated_core_raises_retryable_provider_session_error():
    task, sources = make_task_and_sources()
    proposal = make_valid_proposal()
    divergent = dict(
        proposal,
        summary="divergent summary",
        toolAction="Editing file",
        toolSummary="File edit",
    )
    raw = make_stream_raw(response=f"{json.dumps(proposal)}\n{json.dumps(divergent)}")
    with pytest.raises(RetryableProviderSessionError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert type(exc_info.value) is RetryableProviderSessionError
    assert str(exc_info.value) == "CLI response is not one valid JSON document"


def test_invalid_decoration_types_and_bounds_raise_retryable_provider_session_error():
    task, sources = make_task_and_sources()
    proposal = make_valid_proposal()

    bad_type = dict(proposal, toolAction=123, toolSummary="File edit")
    raw = make_stream_raw(response=f"{json.dumps(proposal)}\n{json.dumps(bad_type)}")
    with pytest.raises(RetryableProviderSessionError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert str(exc_info.value) == "CLI response is not one valid JSON document"

    empty_action = dict(proposal, toolAction="", toolSummary="File edit")
    raw = make_stream_raw(response=f"{json.dumps(proposal)}\n{json.dumps(empty_action)}")
    with pytest.raises(RetryableProviderSessionError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert str(exc_info.value) == "CLI response is not one valid JSON document"

    empty_summary = dict(proposal, toolAction="Editing file", toolSummary="")
    raw = make_stream_raw(response=f"{json.dumps(proposal)}\n{json.dumps(empty_summary)}")
    with pytest.raises(RetryableProviderSessionError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert str(exc_info.value) == "CLI response is not one valid JSON document"

    oversized_action = dict(proposal, toolAction="a" * 257, toolSummary="File edit")
    raw = make_stream_raw(response=f"{json.dumps(proposal)}\n{json.dumps(oversized_action)}")
    with pytest.raises(RetryableProviderSessionError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert str(exc_info.value) == "CLI response is not one valid JSON document"

    oversized_summary = dict(proposal, toolAction="Editing file", toolSummary="b" * 257)
    raw = make_stream_raw(response=f"{json.dumps(proposal)}\n{json.dumps(oversized_summary)}")
    with pytest.raises(RetryableProviderSessionError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert str(exc_info.value) == "CLI response is not one valid JSON document"


def test_valid_exact_256_byte_decoration_accepted():
    task, sources = make_task_and_sources()
    proposal = make_valid_proposal()
    decorated = dict(proposal, toolAction="a" * 256, toolSummary="b" * 256)
    raw = make_stream_raw(response=f"{json.dumps(proposal)}\n{json.dumps(decorated)}")
    result, envelope = parse_stream(raw, task, sources, "verifier")
    assert result == proposal
    assert envelope["status"] == "SUCCESS"


def test_trailing_prose_after_multiple_responses_raises_retryable_provider_session_error():
    task, sources = make_task_and_sources()
    proposal = make_valid_proposal()
    decorated = dict(proposal, toolAction="Editing file", toolSummary="File edit")
    multi_json = f"{json.dumps(proposal)}\n{json.dumps(decorated)}\nSome extra trailing prose"
    raw = make_stream_raw(response=multi_json)
    with pytest.raises(RetryableProviderSessionError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert type(exc_info.value) is RetryableProviderSessionError
    assert str(exc_info.value) == "CLI response is not one valid JSON document"


def test_five_copies_raises_retryable_provider_session_error():
    task, sources = make_task_and_sources()
    proposal = make_valid_proposal()
    decorated = dict(proposal, toolAction="Editing file", toolSummary="File edit")
    five_json = (
        f"{json.dumps(proposal)}\n{json.dumps(proposal)}\n"
        f"{json.dumps(proposal)}\n{json.dumps(proposal)}\n{json.dumps(decorated)}"
    )
    raw = make_stream_raw(response=five_json)
    with pytest.raises(RetryableProviderSessionError) as exc_info:
        parse_stream(raw, task, sources, "verifier")
    assert type(exc_info.value) is RetryableProviderSessionError
    assert str(exc_info.value) == "CLI response is not one valid JSON document"
