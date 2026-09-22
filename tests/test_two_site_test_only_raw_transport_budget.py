from __future__ import annotations

import json

try:
    from scripts.two_site_proposal_runner import (
        _deduplicate_response,
        _deduplicate_response_with_first_document_span,
        _parse_stream,
        sha,
        CANONICAL_RESPONSE_KEYS,
        DECORATION_KEYS,
        MODEL,
        ProposalError,
        RetryableProviderSessionError,
    )
except ImportError:
    from two_site_proposal_runner import (
        _deduplicate_response,
        _deduplicate_response_with_first_document_span,
        _parse_stream,
        sha,
        CANONICAL_RESPONSE_KEYS,
        DECORATION_KEYS,
        MODEL,
        ProposalError,
        RetryableProviderSessionError,
    )


def test_first_document_span_preserves_exact_utf8_bytes():
    raw_first_document = (
        '{\n'
        '  "status" :   "PROPOSAL_READY" ,\n'
        '  "covered_requirements" : [ 1 ] ,\n'
        '  "summary" : "تغییرات\\/سیستم\\u0020با\\u0020نیم\\u200cفاصله" ,\n'
        '  "findings" : [] ,\n'
        '  "changes" : [\n'
        '    {\n'
        '      "path" : "scripts\\/example.py" ,\n'
        '      "original_sha256" : "1355e9731dc401dd2f73831607210ce164884eb2bda373d314b40239303ca3f2" ,\n'
        '      "content" : "print(\'سلام دنیا\')\\n" ,\n'
        '      "replacements" : []\n'
        '    }\n'
        '  ]\n'
        '}'
    )
    expected_mapping = {
        "status": "PROPOSAL_READY",
        "covered_requirements": [1],
        "summary": "تغییرات/سیستم با نیم\u200cفاصله",
        "findings": [],
        "changes": [
            {
                "path": "scripts/example.py",
                "original_sha256": "1355e9731dc401dd2f73831607210ce164884eb2bda373d314b40239303ca3f2",
                "content": "print('سلام دنیا')\n",
                "replacements": [],
            }
        ],
    }
    second_document = json.dumps(expected_mapping, ensure_ascii=False)
    multi_response = f"{raw_first_document}\n\n{second_document}"

    mapping, first_span = _deduplicate_response_with_first_document_span(multi_response)

    assert mapping == expected_mapping
    assert set(mapping.keys()) == CANONICAL_RESPONSE_KEYS
    assert first_span == raw_first_document.encode("utf-8")
    assert first_span != json.dumps(expected_mapping).encode("utf-8")
    assert (
        first_span
        != json.dumps(
            expected_mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    )
    assert b" :   \"PROPOSAL_READY\"" in first_span
    assert b"\\/" in first_span
    assert b"\\u0020" in first_span
    assert b"\\u200c" in first_span
    assert "سلام دنیا".encode("utf-8") in first_span


def test_first_document_span_is_bound_to_first_document_only():
    first_doc_text = (
        '{"status": "PROPOSAL_READY", "summary": "doc 1", '
        '"covered_requirements": [1], "findings": [], "changes": []}'
    )
    second_doc_dict = {
        "status": "PROPOSAL_READY",
        "summary": "doc 1",
        "covered_requirements": [1],
        "findings": [],
        "changes": [],
        "toolAction": "Inspecting repository",
        "toolSummary": "Repository inspection",
    }
    second_doc_text = json.dumps(second_doc_dict)
    leading_ws = "  \t \n\n "
    inter_ws = "\n\n   \t  \n"
    trailing_ws = " \n\t  \n "
    multi_response = f"{leading_ws}{first_doc_text}{inter_ws}{second_doc_text}{trailing_ws}"

    mapping, first_span = _deduplicate_response_with_first_document_span(multi_response)

    assert mapping == {
        "status": "PROPOSAL_READY",
        "summary": "doc 1",
        "covered_requirements": [1],
        "findings": [],
        "changes": [],
    }
    assert set(mapping.keys()) == CANONICAL_RESPONSE_KEYS
    assert first_span == first_doc_text.encode("utf-8")
    assert not first_span.startswith(b" ")
    assert not first_span.startswith(b"\n")
    assert not first_span.startswith(b"\t")
    assert not first_span.endswith(b" ")
    assert not first_span.endswith(b"\n")
    assert not first_span.endswith(b"\t")
    assert b"toolAction" not in first_span
    assert b"toolSummary" not in first_span
    assert b"Inspecting repository" not in first_span
    assert first_span != second_doc_text.encode("utf-8")


def test_legacy_deduplicate_contract_remains_unchanged():
    valid_proposal = {
        "status": "PROPOSAL_READY",
        "summary": "legacy contract check",
        "covered_requirements": [1],
        "findings": [],
        "changes": [],
    }
    valid_multi = f"{json.dumps(valid_proposal)}\n{json.dumps(valid_proposal)}"
    result = _deduplicate_response(valid_multi)

    assert isinstance(result, dict)
    assert not isinstance(result, tuple)
    assert result == valid_proposal
    assert set(result.keys()) == CANONICAL_RESPONSE_KEYS

    differing_proposal = dict(valid_proposal, summary="different summary")
    differing_multi = f"{json.dumps(valid_proposal)}\n{json.dumps(differing_proposal)}"
    try:
        _deduplicate_response(differing_multi)
    except RetryableProviderSessionError as exc:
        assert str(exc) == "CLI response is not one valid JSON document"
    else:
        raise AssertionError("expected RetryableProviderSessionError was not raised")

    single_response = json.dumps(valid_proposal)
    try:
        _deduplicate_response(single_response)
    except RetryableProviderSessionError as exc:
        assert str(exc) == "CLI response is not one valid JSON document"
    else:
        raise AssertionError("expected RetryableProviderSessionError was not raised")


def _make_successful_stream(response: str, session_id: str) -> str:
    return (
        json.dumps({
            "event": "init",
            "conversation_id": session_id,
            "init": {"model": MODEL},
        })
        + "\n"
        + json.dumps({
            "event": "result",
            "result": {
                "conversation_id": session_id,
                "status": "SUCCESS",
                "response": response,
            },
        })
        + "\n"
    )


def test_duplicate_worker_rejects_oversized_first_document_before_normalization():
    task = {
        "task_id": "test-task-reject-oversized",
        "goal": "Test rejecting oversized first document before normalization",
        "context_files": ["tests/test_example.py"],
        "allowed_files": ["tests/test_example.py"],
        "requirements": ["requirement 1"],
        "test_selectors": ["tests/test_example.py::test_case"],
    }
    content = "def test_case():\n    assert True\n"
    content_sha = sha(content.encode("utf-8"))
    sources = {
        "tests/test_example.py": {
            "sha256": content_sha,
            "content": content,
        }
    }
    proposal_dict = {
        "status": "PROPOSAL_READY",
        "summary": "oversized padded proposal",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {
                "path": "tests/test_example.py",
                "original_sha256": content_sha,
                "content": None,
                "replacements": [
                    {
                        "old": "assert True",
                        "new": "assert 1 == 1",
                    }
                ],
            }
        ],
    }
    padding = " " * 65000
    raw_first_document = (
        '{\n'
        '  "status": "PROPOSAL_READY",\n'
        f'  "summary": "oversized padded proposal",{padding}\n'
        '  "covered_requirements": [1],\n'
        '  "findings": [],\n'
        '  "changes": [\n'
        '    {\n'
        '      "path": "tests/test_example.py",\n'
        f'      "original_sha256": "{content_sha}",\n'
        '      "content": null,\n'
        '      "replacements": [\n'
        '        {\n'
        '          "old": "assert True",\n'
        '          "new": "assert 1 == 1"\n'
        '        }\n'
        '      ]\n'
        '    }\n'
        '  ]\n'
        '}'
    )
    second_document = json.dumps(proposal_dict)
    multi_response = f"{raw_first_document}\n{second_document}"

    session_id = "test-session-reject"
    raw_stream = _make_successful_stream(multi_response, session_id)

    try:
        _parse_stream(raw_stream, task, sources, "worker", expected_model=MODEL)
    except ProposalError as exc:
        assert str(exc) == "test-only raw transport exceeds budget"
    else:
        raise AssertionError("expected ProposalError was not raised")


def test_duplicate_worker_accepts_compact_replacement_before_normalization():
    task = {
        "task_id": "test-task-accept-compact-replacement",
        "goal": "Test accepting compact replacement whose materialized content exceeds raw ceiling",
        "context_files": ["tests/test_example.py"],
        "allowed_files": ["tests/test_example.py"],
        "requirements": ["requirement 1"],
        "test_selectors": ["tests/test_example.py::test_case"],
    }
    original_content = (
        "# " + ("a" * 70000) + "\n"
        "def test_case():\n"
        "    assert True\n"
    )
    content_sha = sha(original_content.encode("utf-8"))
    sources = {
        "tests/test_example.py": {
            "sha256": content_sha,
            "content": original_content,
        }
    }
    proposal_dict = {
        "status": "PROPOSAL_READY",
        "summary": "compact replacement proposal",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {
                "path": "tests/test_example.py",
                "original_sha256": content_sha,
                "content": None,
                "replacements": [
                    {
                        "old": "assert True",
                        "new": "assert 1 == 1",
                    }
                ],
            }
        ],
    }
    raw_first_document = json.dumps(proposal_dict)
    second_doc_dict = dict(proposal_dict)
    second_doc_dict["toolAction"] = "Writing file"
    second_doc_dict["toolSummary"] = "File write"
    second_document = json.dumps(second_doc_dict)
    multi_response = f"{raw_first_document}\n{second_document}"

    session_id = "test-session-accept"
    raw_stream = _make_successful_stream(multi_response, session_id)

    result, envelope = _parse_stream(raw_stream, task, sources, "worker", expected_model=MODEL)

    expected_materialized_content = (
        "# " + ("a" * 70000) + "\n"
        "def test_case():\n"
        "    assert 1 == 1\n"
    )
    assert result["status"] == "PROPOSAL_READY"
    assert result["covered_requirements"] == [1]
    assert result["findings"] == []
    assert len(result["changes"]) == 1
    change = result["changes"][0]
    assert change["path"] == "tests/test_example.py"
    assert change["original_sha256"] == content_sha
    assert change["content"] == expected_materialized_content
    assert len(raw_first_document.encode("utf-8")) <= 64000
    assert len(change["content"].encode("utf-8")) > 64000
    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 64000
