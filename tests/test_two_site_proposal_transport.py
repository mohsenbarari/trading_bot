"""Tests for minimal pure transport policy in scripts/two_site_proposal_transport.py."""
from __future__ import annotations

import copy
import hashlib
import pytest

from scripts.two_site_proposal_transport import (
    FULL_CONTENT_BYTE_THRESHOLD,
    TransportPolicyError,
    build_changes_schema,
    classify_source,
    validate_change_mode,
)

SAMPLE_SHA_A = hashlib.sha256(b"content_a").hexdigest()
SAMPLE_SHA_B = hashlib.sha256(b"content_b").hexdigest()


def test_utf8_threshold_boundaries() -> None:
    assert classify_source({"sha256": None, "content": None}) == "new"

    boundary = 100
    small_text = "a" * 99
    exact_large_text = "a" * 100
    assert classify_source({"sha256": SAMPLE_SHA_A, "content": small_text}, threshold=boundary) == "small"
    assert classify_source({"sha256": SAMPLE_SHA_A, "content": exact_large_text}, threshold=boundary) == "large"

    # Multi-byte UTF-8 test ('€' is 3 bytes in UTF-8)
    assert classify_source({"sha256": SAMPLE_SHA_A, "content": "€" * 33}, threshold=100) == "small"
    assert classify_source({"sha256": SAMPLE_SHA_A, "content": "€" * 34}, threshold=100) == "large"

    assert classify_source({"sha256": SAMPLE_SHA_A, "content": ""}, threshold=boundary) == "small"
    assert classify_source({"sha256": SAMPLE_SHA_A, "content": "x" * (FULL_CONTENT_BYTE_THRESHOLD - 1)}) == "small"
    assert classify_source({"sha256": SAMPLE_SHA_A, "content": "x" * FULL_CONTENT_BYTE_THRESHOLD}) == "large"


def test_schema_binding_modes_and_immutability() -> None:
    allowed_files = ["new.py", "small.py", "large.py"]
    sources = {
        "new.py": {"sha256": None, "content": None},
        "small.py": {"sha256": SAMPLE_SHA_A, "content": "print('small')"},
        "large.py": {"sha256": SAMPLE_SHA_B, "content": "x" * 200},
    }
    sources_copy = copy.deepcopy(sources)
    allowed_copy = list(allowed_files)

    schema = build_changes_schema("worker", allowed_files, sources, max_replacements=10, threshold=100)

    assert sources == sources_copy
    assert allowed_files == allowed_copy

    assert schema["type"] == "array"
    branches = schema["items"]["anyOf"]
    assert len(branches) == 4

    b0 = branches[0]
    assert b0["properties"]["path"] == {"enum": ["new.py"]}
    assert b0["properties"]["original_sha256"] == {"type": "null"}
    assert b0["properties"]["content"] == {"type": "string"}
    assert b0["properties"]["replacements"] == {"type": "array", "maxItems": 0}
    assert b0["additionalProperties"] is False

    b1 = branches[1]
    assert b1["properties"]["path"] == {"enum": ["small.py"]}
    assert b1["properties"]["original_sha256"] == {"enum": [SAMPLE_SHA_A]}
    assert b1["properties"]["content"] == {"type": "string"}
    assert b1["properties"]["replacements"] == {"type": "array", "maxItems": 0}

    b2 = branches[2]
    assert b2["properties"]["path"] == {"enum": ["small.py"]}
    assert b2["properties"]["original_sha256"] == {"enum": [SAMPLE_SHA_A]}
    assert b2["properties"]["content"] == {"type": "null"}
    assert b2["properties"]["replacements"]["minItems"] == 1
    assert b2["properties"]["replacements"]["maxItems"] == 10

    b3 = branches[3]
    assert b3["properties"]["path"] == {"enum": ["large.py"]}
    assert b3["properties"]["original_sha256"] == {"enum": [SAMPLE_SHA_B]}
    assert b3["properties"]["content"] == {"type": "null"}
    assert b3["properties"]["replacements"]["maxItems"] == 10

    schema["items"]["anyOf"].clear()
    schema2 = build_changes_schema("worker", allowed_files, sources, max_replacements=10, threshold=100)
    assert len(schema2["items"]["anyOf"]) == 4


def test_verifier_behavior() -> None:
    allowed_files = ["new.py", "existing.py"]
    sources = {
        "new.py": {"sha256": None, "content": None},
        "existing.py": {"sha256": SAMPLE_SHA_A, "content": "data"},
    }
    schema = build_changes_schema("verifier", allowed_files, sources)
    assert schema == {
        "type": "array",
        "maxItems": 0,
        "items": {"type": "object", "additionalProperties": False},
    }

    with pytest.raises(TransportPolicyError):
        build_changes_schema("verifier", [], sources)
    with pytest.raises(TransportPolicyError):
        build_changes_schema("verifier", ["missing.py"], sources)


def test_runtime_transport_matrix() -> None:
    rec_new = {"sha256": None, "content": None}
    rec_small = {"sha256": SAMPLE_SHA_A, "content": "small content"}
    rec_large = {"sha256": SAMPLE_SHA_B, "content": "x" * 200}
    threshold = 100

    full_change = {"path": "f.py", "original_sha256": None, "content": "new text", "replacements": []}
    repl_change = {"path": "f.py", "original_sha256": SAMPLE_SHA_A, "content": None, "replacements": [{"old": "a", "new": "b"}]}
    legacy_change = {"path": "f.py", "original_sha256": SAMPLE_SHA_A, "content": "legacy text"}

    # NEW
    assert validate_change_mode(full_change, rec_new, threshold=threshold) == "full"
    with pytest.raises(TransportPolicyError):
        validate_change_mode(repl_change, rec_new, threshold=threshold)
    with pytest.raises(TransportPolicyError):
        validate_change_mode(legacy_change, rec_new, threshold=threshold)
    bad_new_sha = {"path": "f.py", "original_sha256": SAMPLE_SHA_A, "content": "new text", "replacements": []}
    with pytest.raises(TransportPolicyError):
        validate_change_mode(bad_new_sha, rec_new, threshold=threshold)

    # LARGE
    full_large = {"path": "f.py", "original_sha256": SAMPLE_SHA_B, "content": "x", "replacements": []}
    repl_large = {"path": "f.py", "original_sha256": SAMPLE_SHA_B, "content": None, "replacements": [{"old": "x", "new": "y"}]}
    legacy_large = {"path": "f.py", "original_sha256": SAMPLE_SHA_B, "content": "legacy text"}
    assert validate_change_mode(repl_large, rec_large, threshold=threshold) == "replacement"
    with pytest.raises(TransportPolicyError):
        validate_change_mode(full_large, rec_large, threshold=threshold)
    with pytest.raises(TransportPolicyError):
        validate_change_mode(legacy_large, rec_large, threshold=threshold)
    mismatched_large_repl = {"path": "f.py", "original_sha256": SAMPLE_SHA_A, "content": None, "replacements": [{"old": "x", "new": "y"}]}
    with pytest.raises(TransportPolicyError):
        validate_change_mode(mismatched_large_repl, rec_large, threshold=threshold)

    # SMALL
    full_small = {"path": "f.py", "original_sha256": SAMPLE_SHA_A, "content": "full text", "replacements": []}
    assert validate_change_mode(full_small, rec_small, threshold=threshold) == "full"
    assert validate_change_mode(repl_change, rec_small, threshold=threshold) == "replacement"
    assert validate_change_mode(legacy_change, rec_small, threshold=threshold) == "legacy"

    mismatched_small_full = {"path": "f.py", "original_sha256": SAMPLE_SHA_B, "content": "text", "replacements": []}
    mismatched_small_repl = {"path": "f.py", "original_sha256": SAMPLE_SHA_B, "content": None, "replacements": [{"old": "a", "new": "b"}]}
    mismatched_small_legacy = {"path": "f.py", "original_sha256": SAMPLE_SHA_B, "content": "text"}
    with pytest.raises(TransportPolicyError):
        validate_change_mode(mismatched_small_full, rec_small, threshold=threshold)
    with pytest.raises(TransportPolicyError):
        validate_change_mode(mismatched_small_repl, rec_small, threshold=threshold)
    with pytest.raises(TransportPolicyError):
        validate_change_mode(mismatched_small_legacy, rec_small, threshold=threshold)
    null_sha_existing = {"path": "f.py", "original_sha256": None, "content": "text", "replacements": []}
    with pytest.raises(TransportPolicyError):
        validate_change_mode(null_sha_existing, rec_small, threshold=threshold)

    # Replacement limits
    exceeds = {"path": "f.py", "original_sha256": SAMPLE_SHA_A, "content": None, "replacements": [{"old": f"{i}", "new": f"{i}x"} for i in range(5)]}
    assert validate_change_mode(exceeds, rec_small, max_replacements=5, threshold=threshold) == "replacement"
    with pytest.raises(TransportPolicyError):
        validate_change_mode(exceeds, rec_small, max_replacements=4, threshold=threshold)


def test_malformed_inputs_fail_closed() -> None:
    valid_sources = {"a.py": {"sha256": SAMPLE_SHA_A, "content": "a"}}

    for bad_files in (None, "a.py", [], [123], [""], ["a.py", "a.py"]):
        with pytest.raises(TransportPolicyError):
            build_changes_schema("worker", bad_files, valid_sources)  # type: ignore

    with pytest.raises(TransportPolicyError):
        build_changes_schema("worker", ["a.py"], {"a.py": {"sha256": "bad_sha", "content": "a"}})
    with pytest.raises(TransportPolicyError):
        build_changes_schema("worker", ["a.py"], {"a.py": {"sha256": None, "content": "not_none"}})
    with pytest.raises(TransportPolicyError):
        build_changes_schema("worker", ["a.py"], {"a.py": {"extra": 1}})

    for bad_int in (0, -1, True, False, 1.5, "10"):
        with pytest.raises(TransportPolicyError):
            classify_source(valid_sources["a.py"], threshold=bad_int)  # type: ignore
        with pytest.raises(TransportPolicyError):
            build_changes_schema("worker", ["a.py"], valid_sources, max_replacements=bad_int)  # type: ignore

    with pytest.raises(TransportPolicyError):
        build_changes_schema("invalid_role", ["a.py"], valid_sources)

    rec = valid_sources["a.py"]
    for bad_change in (
        None,
        [],
        {},
        {"path": "a.py"},
        {"path": "a.py", "original_sha256": SAMPLE_SHA_A, "content": "x", "extra": 1},
        {"path": "", "original_sha256": SAMPLE_SHA_A, "content": "x", "replacements": []},
        {"path": "a.py", "original_sha256": 123, "content": "x", "replacements": []},
        {"path": "a.py", "original_sha256": SAMPLE_SHA_A, "content": 123, "replacements": []},
        {"path": "a.py", "original_sha256": SAMPLE_SHA_A, "content": "x", "replacements": [{"old": "a", "new": "b"}]},
        {"path": "a.py", "original_sha256": SAMPLE_SHA_A, "content": None, "replacements": []},
        {"path": "a.py", "original_sha256": SAMPLE_SHA_A, "content": None, "replacements": "not_a_list"},
    ):
        with pytest.raises(TransportPolicyError):
            validate_change_mode(bad_change, rec)
