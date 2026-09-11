"""Unit tests for two_site_correction_delta."""
from __future__ import annotations

import copy
import hashlib
import pytest

from scripts.two_site_correction_delta import (
    CorrectionDeltaError,
    build_candidate_sources,
    rebase_correction_result,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_build_candidate_sources_prior_new_and_existing_overlay():
    allowed_files = ["new_file.py", "existing_file.py", "untouched_file.py"]
    orig_exist_content = "def existing():\n    return 1\n"
    orig_exist_sha = _sha(orig_exist_content)
    orig_untouched_content = "def untouched():\n    return 'u'\n"
    orig_untouched_sha = _sha(orig_untouched_content)

    original_sources = {
        "new_file.py": {"sha256": None, "content": None},
        "existing_file.py": {"sha256": orig_exist_sha, "content": orig_exist_content},
        "untouched_file.py": {"sha256": orig_untouched_sha, "content": orig_untouched_content},
    }

    prior_changes = [
        {"path": "new_file.py", "original_sha256": None, "content": "def new():\n    return 2\n"},
        {"path": "existing_file.py", "original_sha256": orig_exist_sha, "content": "def existing():\n    return 42\n"},
    ]

    candidate = build_candidate_sources(allowed_files, original_sources, prior_changes)

    assert candidate["new_file.py"] == {
        "sha256": _sha("def new():\n    return 2\n"),
        "content": "def new():\n    return 2\n",
    }
    assert candidate["existing_file.py"] == {
        "sha256": _sha("def existing():\n    return 42\n"),
        "content": "def existing():\n    return 42\n",
    }
    assert candidate["untouched_file.py"] == {
        "sha256": orig_untouched_sha,
        "content": orig_untouched_content,
    }


def test_rebase_untouched_retention_and_added_path():
    allowed_files = ["f1.py", "f2.py", "f3.py"]
    f1_orig = "f1 original\n"
    f2_orig = "f2 original\n"
    original_sources = {
        "f1.py": {"sha256": _sha(f1_orig), "content": f1_orig},
        "f2.py": {"sha256": _sha(f2_orig), "content": f2_orig},
        "f3.py": {"sha256": None, "content": None},
    }
    prior_changes = [
        {"path": "f1.py", "original_sha256": _sha(f1_orig), "content": "f1 prior\n"},
    ]
    candidate = build_candidate_sources(allowed_files, original_sources, prior_changes)

    delta_result = {
        "status": "PROPOSAL_READY",
        "summary": "delta fixes f2 and adds f3 while retaining f1",
        "covered_requirements": [1, 2],
        "findings": [],
        "changes": [
            {"path": "f2.py", "original_sha256": _sha(f2_orig), "content": "f2 delta\n"},
            {"path": "f3.py", "original_sha256": None, "content": "f3 delta\n"},
        ],
    }

    rebased = rebase_correction_result(delta_result, allowed_files, original_sources, candidate)

    assert rebased["status"] == "PROPOSAL_READY"
    assert rebased["summary"] == delta_result["summary"]
    assert rebased["covered_requirements"] == [1, 2]
    assert rebased["findings"] == []
    assert rebased["changes"] == [
        {"path": "f1.py", "original_sha256": _sha(f1_orig), "content": "f1 prior\n"},
        {"path": "f2.py", "original_sha256": _sha(f2_orig), "content": "f2 delta\n"},
        {"path": "f3.py", "original_sha256": None, "content": "f3 delta\n"},
    ]


def test_rebase_exact_revert_omission():
    allowed_files = ["a.py", "b.py"]
    a_orig = "a_original\n"
    b_orig = "b_original\n"
    original_sources = {
        "a.py": {"sha256": _sha(a_orig), "content": a_orig},
        "b.py": {"sha256": _sha(b_orig), "content": b_orig},
    }
    prior_changes = [
        {"path": "a.py", "original_sha256": _sha(a_orig), "content": "a_prior\n"},
        {"path": "b.py", "original_sha256": _sha(b_orig), "content": "b_prior\n"},
    ]
    candidate = build_candidate_sources(allowed_files, original_sources, prior_changes)

    delta_result = {
        "status": "PROPOSAL_READY",
        "summary": "revert a.py to original bytes",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {"path": "a.py", "original_sha256": _sha("a_prior\n"), "content": a_orig},
        ],
    }

    rebased = rebase_correction_result(delta_result, allowed_files, original_sources, candidate)
    assert rebased["changes"] == [
        {"path": "b.py", "original_sha256": _sha(b_orig), "content": "b_prior\n"},
    ]


def test_rebase_ordering_and_original_sha():
    allowed_files = ["z.py", "a.py", "m.py"]
    original_sources = {
        "z.py": {"sha256": None, "content": None},
        "a.py": {"sha256": _sha("a_orig\n"), "content": "a_orig\n"},
        "m.py": {"sha256": _sha("m_orig\n"), "content": "m_orig\n"},
    }
    prior_changes = [
        {"path": "m.py", "original_sha256": _sha("m_orig\n"), "content": "m_prior\n"},
        {"path": "a.py", "original_sha256": _sha("a_orig\n"), "content": "a_prior\n"},
    ]
    candidate = build_candidate_sources(allowed_files, original_sources, prior_changes)

    delta_result = {
        "status": "PROPOSAL_READY",
        "summary": "order test",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {"path": "z.py", "original_sha256": None, "content": "z_delta\n"},
            {"path": "m.py", "original_sha256": _sha("m_prior\n"), "content": "m_delta\n"},
        ],
    }

    rebased = rebase_correction_result(delta_result, allowed_files, original_sources, candidate)
    paths = [c["path"] for c in rebased["changes"]]
    assert paths == ["z.py", "a.py", "m.py"]
    assert rebased["changes"][0]["original_sha256"] is None
    assert rebased["changes"][1]["original_sha256"] == _sha("a_orig\n")
    assert rebased["changes"][2]["original_sha256"] == _sha("m_orig\n")


def test_immutability():
    allowed_files = ["f1.py", "f2.py"]
    f1_orig = "f1\n"
    f2_orig = "f2\n"
    original_sources = {
        "f1.py": {"sha256": _sha(f1_orig), "content": f1_orig},
        "f2.py": {"sha256": _sha(f2_orig), "content": f2_orig},
    }
    prior_changes = [
        {"path": "f1.py", "original_sha256": _sha(f1_orig), "content": "f1_prior\n"},
    ]
    orig_copy = copy.deepcopy(original_sources)
    prior_copy = copy.deepcopy(prior_changes)
    allowed_copy = copy.deepcopy(allowed_files)

    candidate = build_candidate_sources(allowed_files, original_sources, prior_changes)

    assert original_sources == orig_copy
    assert prior_changes == prior_copy
    assert allowed_files == allowed_copy

    delta_result = {
        "status": "PROPOSAL_READY",
        "summary": "summary",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {"path": "f2.py", "original_sha256": _sha(f2_orig), "content": "f2_delta\n"},
        ],
    }
    result_copy = copy.deepcopy(delta_result)
    cand_copy = copy.deepcopy(candidate)

    _ = rebase_correction_result(delta_result, allowed_files, original_sources, candidate)

    assert delta_result == result_copy
    assert candidate == cand_copy
    assert original_sources == orig_copy
    assert allowed_files == allowed_copy


def test_hash_binding_guards():
    allowed_files = ["f.py"]
    orig_content = "code\n"
    original_sources = {"f.py": {"sha256": _sha(orig_content), "content": orig_content}}

    with pytest.raises(CorrectionDeltaError, match="SHA mismatch"):
        build_candidate_sources(
            allowed_files,
            original_sources,
            [{"path": "f.py", "original_sha256": "0" * 64, "content": "new code\n"}],
        )

    candidate = build_candidate_sources(
        allowed_files,
        original_sources,
        [{"path": "f.py", "original_sha256": _sha(orig_content), "content": "prior code\n"}],
    )

    delta_result = {
        "status": "PROPOSAL_READY",
        "summary": "bad sha",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {"path": "f.py", "original_sha256": _sha(orig_content), "content": "delta code\n"},
        ],
    }
    with pytest.raises(CorrectionDeltaError, match="SHA mismatch"):
        rebase_correction_result(delta_result, allowed_files, original_sources, candidate)


def test_duplicate_out_of_scope_unchanged_empty_malformed_inputs():
    allowed_files = ["a.py", "b.py"]
    original_sources = {
        "a.py": {"sha256": _sha("a\n"), "content": "a\n"},
        "b.py": {"sha256": _sha("b\n"), "content": "b\n"},
    }

    with pytest.raises(CorrectionDeltaError, match="allowed_files"):
        build_candidate_sources([], original_sources, [])

    with pytest.raises(CorrectionDeltaError, match="duplicate"):
        build_candidate_sources(["a.py", "a.py"], original_sources, [])

    with pytest.raises(CorrectionDeltaError, match="duplicate"):
        build_candidate_sources(
            allowed_files,
            original_sources,
            [
                {"path": "a.py", "original_sha256": _sha("a\n"), "content": "a1\n"},
                {"path": "a.py", "original_sha256": _sha("a\n"), "content": "a2\n"},
            ],
        )

    with pytest.raises(CorrectionDeltaError, match="outside allowed files"):
        build_candidate_sources(
            allowed_files,
            original_sources,
            [{"path": "c.py", "original_sha256": "0" * 64, "content": "c\n"}],
        )

    with pytest.raises(CorrectionDeltaError, match="not bound"):
        build_candidate_sources(["a.py", "missing.py"], original_sources, [])

    with pytest.raises(CorrectionDeltaError, match="no byte change"):
        build_candidate_sources(
            allowed_files,
            original_sources,
            [{"path": "a.py", "original_sha256": _sha("a\n"), "content": "a\n"}],
        )

    with pytest.raises(CorrectionDeltaError, match="nonempty string"):
        build_candidate_sources(
            allowed_files,
            original_sources,
            [{"path": "a.py", "original_sha256": _sha("a\n"), "content": "   "}],
        )

    bad_sources = {"a.py": {"sha256": ("A" * 64), "content": "a\n"}, "b.py": original_sources["b.py"]}
    with pytest.raises(CorrectionDeltaError, match="invalid source record"):
        build_candidate_sources(allowed_files, bad_sources, [])

    bad_sources2 = {"a.py": {"sha256": _sha("a\n"), "content": "a\n", "extra": 1}, "b.py": original_sources["b.py"]}
    with pytest.raises(CorrectionDeltaError, match="invalid source record shape"):
        build_candidate_sources(allowed_files, bad_sources2, [])

    candidate = build_candidate_sources(
        allowed_files,
        original_sources,
        [{"path": "a.py", "original_sha256": _sha("a\n"), "content": "a_prior\n"}],
    )

    with pytest.raises(CorrectionDeltaError, match="canonical response keys"):
        rebase_correction_result({"status": "PROPOSAL_READY"}, allowed_files, original_sources, candidate)

    bad_status_result = {
        "status": "BLOCKED",
        "summary": "blocked",
        "covered_requirements": [],
        "findings": ["some error"],
        "changes": [],
    }
    with pytest.raises(CorrectionDeltaError, match="status must be PROPOSAL_READY"):
        rebase_correction_result(bad_status_result, allowed_files, original_sources, candidate)

    unchanged_delta = {
        "status": "PROPOSAL_READY",
        "summary": "unchanged",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {"path": "b.py", "original_sha256": _sha("b\n"), "content": "b\n"},
        ],
    }
    with pytest.raises(CorrectionDeltaError, match="no byte change"):
        rebase_correction_result(unchanged_delta, allowed_files, original_sources, candidate)

    empty_delta = {
        "status": "PROPOSAL_READY",
        "summary": "empty",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {"path": "b.py", "original_sha256": _sha("b\n"), "content": ""},
        ],
    }
    with pytest.raises(CorrectionDeltaError, match="nonempty string"):
        rebase_correction_result(empty_delta, allowed_files, original_sources, candidate)


def test_complete_no_op_rejection():
    allowed_files = ["f.py"]
    orig_content = "original\n"
    original_sources = {"f.py": {"sha256": _sha(orig_content), "content": orig_content}}
    prior_changes = [{"path": "f.py", "original_sha256": _sha(orig_content), "content": "prior\n"}]
    candidate = build_candidate_sources(allowed_files, original_sources, prior_changes)

    delta_result = {
        "status": "PROPOSAL_READY",
        "summary": "exact revert of all changes",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {"path": "f.py", "original_sha256": _sha("prior\n"), "content": orig_content},
        ],
    }
    with pytest.raises(CorrectionDeltaError, match="complete no-op rejection"):
        rebase_correction_result(delta_result, allowed_files, original_sources, candidate)
