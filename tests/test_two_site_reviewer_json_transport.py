"""Real parser regressions; no provider, application or operational data."""
import json

import pytest

from scripts import two_site_proposal_runner as runner


TASK = {
    "task_id": "REVIEW-JSON-001", "goal": "Preserve exact review details.",
    "context_files": [], "allowed_files": ["test_new.py"],
    "requirements": ["Preserve exact review details"], "test_selectors": ["test_new.py"],
}
EXACT = 'بازبینی `"gold"` و مسیر C:\\data\nخط دوم'


def parse(response):
    events = [
        {"event": "init", "conversation_id": "session-one", "init": {"model": runner.MODEL}},
        {"event": "result", "result": {
            "status": "SUCCESS", "conversation_id": "session-one", "response": response,
        }},
    ]
    return runner.parse_stream(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events), TASK, {}, "verifier",
    )[0]


def test_valid_json_preserves_exact_unicode_quotes_backslashes_and_newline():
    value = {"status": "VERIFIED", "summary": EXACT, "covered_requirements": [1],
             "findings": [], "changes": []}
    assert parse(json.dumps(value, ensure_ascii=False)) == value


def test_raw_nested_quotes_are_rejected_instead_of_repaired_or_accepted():
    response = ('{"status":"VERIFIED","summary":"bad "gold" literal",'
                '"covered_requirements":[1],"findings":[],"changes":[]}')
    with pytest.raises(runner.RetryableProviderSessionError, match="valid JSON document"):
        parse(response)


def test_exact_quoted_finding_remains_changes_required():
    value = {"status": "CHANGES_REQUIRED", "summary": "Exact mismatch.",
             "covered_requirements": [1], "findings": [EXACT], "changes": []}
    assert parse(json.dumps(value, ensure_ascii=False)) == value
