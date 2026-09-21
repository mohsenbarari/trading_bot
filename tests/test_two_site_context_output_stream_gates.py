import json

import pytest

from scripts.two_site_proposal_runner import (
    ProposalError,
    _parse_stream,
)


def test_parse_stream_rejects_error_model_output_truncated_with_structured_output():
    task = {
        "task_id": "task-truncation",
        "goal": "Check truncation rejection.",
        "context_files": [],
        "allowed_files": ["scripts/app.py"],
        "requirements": ["Do task."],
        "test_selectors": ["pytest -q"],
    }
    sources = {
        "scripts/app.py": {"sha256": None, "content": None},
    }
    fake_structured_output = {
        "status": "PROPOSAL_READY",
        "summary": "Incomplete partial output.",
        "covered_requirements": [1],
        "findings": [],
        "changes": [{
            "path": "scripts/app.py",
            "original_sha256": None,
            "content": "print('hello')\n",
        }],
    }
    stream_lines = [
        json.dumps({"event": "init", "conversation_id": "test-session", "init": {"model": "gemini-2.5-pro"}}),
        json.dumps({
            "event": "result",
            "conversation_id": "test-session",
            "result": {
                "conversation_id": "test-session",
                "status": "ERROR",
                "error": "Agent execution terminated due to MODEL_OUTPUT_TRUNCATED: output tokens limit exceeded",
                "structured_output": fake_structured_output,
            },
        }),
    ]
    raw_stream = "\n".join(stream_lines) + "\n"
    with pytest.raises(ProposalError) as exc:
        _parse_stream(raw_stream, task, sources, "worker", expected_model="gemini-2.5-pro")
    assert "MODEL_OUTPUT_TRUNCATED" in str(exc.value)
