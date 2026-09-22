from __future__ import annotations

import json

try:
    from scripts.two_site_proposal_runner import (
        _deduplicate_response,
        _deduplicate_response_with_first_document_span,
        CANONICAL_RESPONSE_KEYS,
        DECORATION_KEYS,
        RetryableProviderSessionError,
    )
except ImportError:
    from two_site_proposal_runner import (
        _deduplicate_response,
        _deduplicate_response_with_first_document_span,
        CANONICAL_RESPONSE_KEYS,
        DECORATION_KEYS,
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
