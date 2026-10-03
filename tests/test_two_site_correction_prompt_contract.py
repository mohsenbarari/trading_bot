"""Tests for correction prompt contract, subset transport schema, and rebase isolation."""
from __future__ import annotations

import copy
import hashlib
from typing import Any

import pytest

from scripts.two_site_correction_delta import (
    CorrectionDeltaError,
    build_candidate_sources,
    rebase_correction_result,
)
from scripts.two_site_proposal_transport import (
    TransportPolicyError,
    build_changes_schema,
    validate_change_mode,
)

# ---------------------------------------------------------------------------
# Independent frozen fixtures & expectations
# ---------------------------------------------------------------------------

PATH_NEW_CORR = "tests/test_new_corrected.py"
PATH_NEW_OMIT = "tests/test_new_omitted.py"
PATH_EXIST_OMIT = "docs/existing_omitted.md"
PATH_UNTOUCHED = "docs/untouched.md"

ALLOWED_FILES = [
    PATH_NEW_CORR,
    PATH_NEW_OMIT,
    PATH_EXIST_OMIT,
    PATH_UNTOUCHED,
]

CONTENT_EXIST_ORIG = "original existing content\n"
SHA_EXIST_ORIG = hashlib.sha256(CONTENT_EXIST_ORIG.encode("utf-8")).hexdigest()

CONTENT_UNTOUCHED = "untouched baseline content\n"
SHA_UNTOUCHED = hashlib.sha256(CONTENT_UNTOUCHED.encode("utf-8")).hexdigest()

CONTENT_NEW_CORR_V1 = "def test_corr_v1():\n    pass\n"
SHA_NEW_CORR_V1 = hashlib.sha256(CONTENT_NEW_CORR_V1.encode("utf-8")).hexdigest()

CONTENT_NEW_OMIT_V1 = "def test_omit_v1():\n    pass\n"
SHA_NEW_OMIT_V1 = hashlib.sha256(CONTENT_NEW_OMIT_V1.encode("utf-8")).hexdigest()

CONTENT_EXIST_OMIT_V1 = "prior modified existing content\n"
SHA_EXIST_OMIT_V1 = hashlib.sha256(CONTENT_EXIST_OMIT_V1.encode("utf-8")).hexdigest()

CONTENT_NEW_CORR_V2 = "def test_corr_v2():\n    assert True\n"

ORIGINAL_SOURCES = {
    PATH_NEW_CORR: {"sha256": None, "content": None},
    PATH_NEW_OMIT: {"sha256": None, "content": None},
    PATH_EXIST_OMIT: {"sha256": SHA_EXIST_ORIG, "content": CONTENT_EXIST_ORIG},
    PATH_UNTOUCHED: {"sha256": SHA_UNTOUCHED, "content": CONTENT_UNTOUCHED},
}

PRIOR_CHANGES = [
    {"path": PATH_NEW_CORR, "original_sha256": None, "content": CONTENT_NEW_CORR_V1},
    {"path": PATH_NEW_OMIT, "original_sha256": None, "content": CONTENT_NEW_OMIT_V1},
    {"path": PATH_EXIST_OMIT, "original_sha256": SHA_EXIST_ORIG, "content": CONTENT_EXIST_OMIT_V1},
]

EXPECTED_CANDIDATE_SOURCES = {
    PATH_NEW_CORR: {"sha256": SHA_NEW_CORR_V1, "content": CONTENT_NEW_CORR_V1},
    PATH_NEW_OMIT: {"sha256": SHA_NEW_OMIT_V1, "content": CONTENT_NEW_OMIT_V1},
    PATH_EXIST_OMIT: {"sha256": SHA_EXIST_OMIT_V1, "content": CONTENT_EXIST_OMIT_V1},
    PATH_UNTOUCHED: {"sha256": SHA_UNTOUCHED, "content": CONTENT_UNTOUCHED},
}

RAW_SUBSET_CORRECTION = {
    "status": "PROPOSAL_READY",
    "summary": "Fix candidate defect in new test",
    "covered_requirements": [1, 2, 3],
    "findings": [],
    "changes": [
        {
            "path": PATH_NEW_CORR,
            "original_sha256": SHA_NEW_CORR_V1,
            "content": CONTENT_NEW_CORR_V2,
            "replacements": [],
        }
    ],
}

EXPECTED_REBASED_RESULT = {
    "status": "PROPOSAL_READY",
    "summary": "Fix candidate defect in new test",
    "covered_requirements": [1, 2, 3],
    "findings": [],
    "changes": [
        {
            "path": PATH_NEW_CORR,
            "original_sha256": None,
            "content": CONTENT_NEW_CORR_V2,
        },
        {
            "path": PATH_NEW_OMIT,
            "original_sha256": None,
            "content": CONTENT_NEW_OMIT_V1,
        },
        {
            "path": PATH_EXIST_OMIT,
            "original_sha256": SHA_EXIST_ORIG,
            "content": CONTENT_EXIST_OMIT_V1,
        },
    ],
}

FROZEN_ALLOWED_FILES = copy.deepcopy(ALLOWED_FILES)
FROZEN_ORIGINAL_SOURCES = copy.deepcopy(ORIGINAL_SOURCES)
FROZEN_PRIOR_CHANGES = copy.deepcopy(PRIOR_CHANGES)
FROZEN_EXPECTED_CANDIDATE_SOURCES = copy.deepcopy(EXPECTED_CANDIDATE_SOURCES)
FROZEN_RAW_SUBSET_CORRECTION = copy.deepcopy(RAW_SUBSET_CORRECTION)
FROZEN_EXPECTED_REBASED_RESULT = copy.deepcopy(EXPECTED_REBASED_RESULT)


def _project_to_canonical(raw_proposal: dict[str, Any]) -> dict[str, Any]:
    """Test-local transparent 3-key canonical projection after mode validation."""
    return {
        "status": raw_proposal["status"],
        "summary": raw_proposal["summary"],
        "covered_requirements": list(raw_proposal["covered_requirements"]),
        "findings": list(raw_proposal["findings"]),
        "changes": [
            {
                "path": c["path"],
                "original_sha256": c["original_sha256"],
                "content": c["content"],
            }
            for c in raw_proposal["changes"]
        ],
    }


def _assert_inputs_unchanged(
    allowed_files: list[str],
    original_sources: dict[str, dict[str, str | None]],
    prior_changes: list[dict[str, Any]],
    candidate_sources: dict[str, dict[str, str | None]],
    raw_proposal: dict[str, Any],
) -> None:
    assert allowed_files == FROZEN_ALLOWED_FILES
    assert original_sources == FROZEN_ORIGINAL_SOURCES
    assert prior_changes == FROZEN_PRIOR_CHANGES
    assert candidate_sources == FROZEN_EXPECTED_CANDIDATE_SOURCES
    assert raw_proposal == FROZEN_RAW_SUBSET_CORRECTION


# ---------------------------------------------------------------------------
# R1: Prompt contract assertions
# ---------------------------------------------------------------------------

def test_prompt_contract_bounded_read():
    prompt_path = "docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md"
    with open(prompt_path, "rb") as f:
        data = f.read(32001)
    assert len(data) < 32001
    text = data.decode("utf-8")

    # Required literal phrases
    assert "constrains transport mode, not membership in changes" in text
    assert "Only paths whose final bytes actually differ" in text
    assert "Omit unchanged listed paths" in text
    assert "Do not manufacture unrelated byte changes" in text
    assert "complete final proposal" in text

    # Preserved conflicting-text absence
    assert "For every listed path, return" not in text


# ---------------------------------------------------------------------------
# R2: Subset transport schema and rebase isolation
# ---------------------------------------------------------------------------

def test_subset_schema_and_rebase_success():
    allowed_files = copy.deepcopy(FROZEN_ALLOWED_FILES)
    original_sources = copy.deepcopy(FROZEN_ORIGINAL_SOURCES)
    prior_changes = copy.deepcopy(FROZEN_PRIOR_CHANGES)
    raw_proposal = copy.deepcopy(FROZEN_RAW_SUBSET_CORRECTION)

    candidate_sources = build_candidate_sources(allowed_files, original_sources, prior_changes)
    assert candidate_sources == FROZEN_EXPECTED_CANDIDATE_SOURCES

    # Verify logical detachment
    assert candidate_sources is not original_sources
    assert candidate_sources[PATH_UNTOUCHED] is not original_sources[PATH_UNTOUCHED]

    # Schema allows subset full-mode paths
    forced_full = [PATH_NEW_CORR, PATH_NEW_OMIT]
    schema = build_changes_schema(
        "worker", allowed_files, candidate_sources, force_full_paths=forced_full
    )
    assert schema["type"] == "array"
    assert "minItems" not in schema

    # Each originally-new forced branch has candidate-SHA enum, minLength=1, maxItems=0
    branches = schema["items"]["anyOf"]
    for path in forced_full:
        path_branches = [
            b for b in branches if b["properties"]["path"]["enum"] == [path]
        ]
        assert len(path_branches) == 1
        branch = path_branches[0]
        assert branch["properties"]["original_sha256"] == {"enum": [candidate_sources[path]["sha256"]]}
        assert branch["properties"]["content"] == {"type": "string", "minLength": 1}
        assert branch["properties"]["replacements"] == {"type": "array", "maxItems": 0}

    # Validate mode on included changed raw 4-key transport
    raw_change = raw_proposal["changes"][0]
    assert raw_change["original_sha256"] is not None
    assert raw_change["original_sha256"] == candidate_sources[PATH_NEW_CORR]["sha256"]
    mode = validate_change_mode(raw_change, candidate_sources[PATH_NEW_CORR], force_full=True)
    assert mode == "full"

    # Project to canonical 3-key and rebase
    canonical_proposal = _project_to_canonical(raw_proposal)
    rebased = rebase_correction_result(
        canonical_proposal, allowed_files, original_sources, candidate_sources
    )

    # Assert whole rebased result against independently frozen value
    assert rebased == FROZEN_EXPECTED_REBASED_RESULT
    assert rebased["changes"] is not canonical_proposal["changes"]

    # Verify retention, untouched omission, and null SHA restoration
    rebased_paths = [c["path"] for c in rebased["changes"]]
    assert rebased_paths == [PATH_NEW_CORR, PATH_NEW_OMIT, PATH_EXIST_OMIT]
    assert PATH_UNTOUCHED not in rebased_paths
    assert rebased["changes"][0]["original_sha256"] is None
    assert rebased["changes"][1]["original_sha256"] is None
    assert rebased["changes"][2]["original_sha256"] == SHA_EXIST_ORIG

    _assert_inputs_unchanged(
        allowed_files, original_sources, prior_changes, candidate_sources, raw_proposal
    )


# ---------------------------------------------------------------------------
# R3: Per-row negative tests with sandwich pattern
# ---------------------------------------------------------------------------

def test_negative_unchanged_candidate_content():
    allowed = copy.deepcopy(FROZEN_ALLOWED_FILES)
    orig = copy.deepcopy(FROZEN_ORIGINAL_SOURCES)
    prior = copy.deepcopy(FROZEN_PRIOR_CHANGES)
    cand = build_candidate_sources(allowed, orig, prior)
    raw = copy.deepcopy(FROZEN_RAW_SUBSET_CORRECTION)

    # Valid before
    assert validate_change_mode(raw["changes"][0], cand[PATH_NEW_CORR], force_full=True) == "full"
    assert rebase_correction_result(_project_to_canonical(raw), allowed, orig, cand) == FROZEN_EXPECTED_REBASED_RESULT

    # ONE mutation: candidate content unchanged
    mutated = copy.deepcopy(raw)
    mutated["changes"][0]["content"] = cand[PATH_NEW_CORR]["content"]

    # Transport validator does NOT reject unchanged bytes
    assert validate_change_mode(mutated["changes"][0], cand[PATH_NEW_CORR], force_full=True) == "full"
    # Rebase rejects unchanged candidate content
    with pytest.raises(CorrectionDeltaError, match="produces no byte change"):
        rebase_correction_result(_project_to_canonical(mutated), allowed, orig, cand)

    _assert_inputs_unchanged(allowed, orig, prior, cand, raw)

    # Valid after
    assert rebase_correction_result(_project_to_canonical(raw), allowed, orig, cand) == FROZEN_EXPECTED_REBASED_RESULT


def test_negative_wrong_candidate_sha():
    allowed = copy.deepcopy(FROZEN_ALLOWED_FILES)
    orig = copy.deepcopy(FROZEN_ORIGINAL_SOURCES)
    prior = copy.deepcopy(FROZEN_PRIOR_CHANGES)
    cand = build_candidate_sources(allowed, orig, prior)
    raw = copy.deepcopy(FROZEN_RAW_SUBSET_CORRECTION)

    # Valid before
    assert validate_change_mode(raw["changes"][0], cand[PATH_NEW_CORR], force_full=True) == "full"
    assert rebase_correction_result(_project_to_canonical(raw), allowed, orig, cand) == FROZEN_EXPECTED_REBASED_RESULT

    # ONE mutation: wrong candidate SHA
    mutated = copy.deepcopy(raw)
    mutated["changes"][0]["original_sha256"] = "a" * 64

    # Mode validator rejects wrong SHA
    with pytest.raises(TransportPolicyError, match="does not match source record"):
        validate_change_mode(mutated["changes"][0], cand[PATH_NEW_CORR], force_full=True)

    _assert_inputs_unchanged(allowed, orig, prior, cand, raw)

    # Valid after
    assert rebase_correction_result(_project_to_canonical(raw), allowed, orig, cand) == FROZEN_EXPECTED_REBASED_RESULT


def test_negative_empty_final_content():
    allowed = copy.deepcopy(FROZEN_ALLOWED_FILES)
    orig = copy.deepcopy(FROZEN_ORIGINAL_SOURCES)
    prior = copy.deepcopy(FROZEN_PRIOR_CHANGES)
    cand = build_candidate_sources(allowed, orig, prior)
    raw = copy.deepcopy(FROZEN_RAW_SUBSET_CORRECTION)

    # Valid before
    assert validate_change_mode(raw["changes"][0], cand[PATH_NEW_CORR], force_full=True) == "full"
    assert rebase_correction_result(_project_to_canonical(raw), allowed, orig, cand) == FROZEN_EXPECTED_REBASED_RESULT

    # ONE mutation: empty final content
    mutated = copy.deepcopy(raw)
    mutated["changes"][0]["content"] = ""

    # Mode validator rejects empty content under force_full
    with pytest.raises(TransportPolicyError, match="force_full requires nonempty content"):
        validate_change_mode(mutated["changes"][0], cand[PATH_NEW_CORR], force_full=True)

    _assert_inputs_unchanged(allowed, orig, prior, cand, raw)

    # Valid after
    assert rebase_correction_result(_project_to_canonical(raw), allowed, orig, cand) == FROZEN_EXPECTED_REBASED_RESULT


def test_negative_out_of_scope_path():
    allowed = copy.deepcopy(FROZEN_ALLOWED_FILES)
    orig = copy.deepcopy(FROZEN_ORIGINAL_SOURCES)
    prior = copy.deepcopy(FROZEN_PRIOR_CHANGES)
    cand = build_candidate_sources(allowed, orig, prior)
    raw = copy.deepcopy(FROZEN_RAW_SUBSET_CORRECTION)

    # Valid before
    assert validate_change_mode(raw["changes"][0], cand[PATH_NEW_CORR], force_full=True) == "full"
    assert rebase_correction_result(_project_to_canonical(raw), allowed, orig, cand) == FROZEN_EXPECTED_REBASED_RESULT

    # ONE mutation: path out of scope
    mutated = copy.deepcopy(raw)
    mutated["changes"][0]["path"] = "tests/test_out_of_scope.py"

    # Rebase rejects out-of-scope path
    with pytest.raises(CorrectionDeltaError, match="outside allowed files"):
        rebase_correction_result(_project_to_canonical(mutated), allowed, orig, cand)

    _assert_inputs_unchanged(allowed, orig, prior, cand, raw)

    # Valid after
    assert rebase_correction_result(_project_to_canonical(raw), allowed, orig, cand) == FROZEN_EXPECTED_REBASED_RESULT


def test_negative_legacy_transport_for_originally_new():
    allowed = copy.deepcopy(FROZEN_ALLOWED_FILES)
    orig = copy.deepcopy(FROZEN_ORIGINAL_SOURCES)
    prior = copy.deepcopy(FROZEN_PRIOR_CHANGES)
    cand = build_candidate_sources(allowed, orig, prior)
    raw = copy.deepcopy(FROZEN_RAW_SUBSET_CORRECTION)

    # Valid before
    assert validate_change_mode(raw["changes"][0], cand[PATH_NEW_CORR], force_full=True) == "full"
    assert rebase_correction_result(_project_to_canonical(raw), allowed, orig, cand) == FROZEN_EXPECTED_REBASED_RESULT

    # ONE mutation: legacy 3-key transport
    mutated_change = {
        "path": PATH_NEW_CORR,
        "original_sha256": cand[PATH_NEW_CORR]["sha256"],
        "content": CONTENT_NEW_CORR_V2,
    }

    # Mode validator rejects legacy mode under force_full
    with pytest.raises(TransportPolicyError, match="force_full does not permit legacy change mode"):
        validate_change_mode(mutated_change, cand[PATH_NEW_CORR], force_full=True)

    _assert_inputs_unchanged(allowed, orig, prior, cand, raw)

    # Valid after
    assert rebase_correction_result(_project_to_canonical(raw), allowed, orig, cand) == FROZEN_EXPECTED_REBASED_RESULT


def test_negative_replacement_transport_for_originally_new():
    allowed = copy.deepcopy(FROZEN_ALLOWED_FILES)
    orig = copy.deepcopy(FROZEN_ORIGINAL_SOURCES)
    prior = copy.deepcopy(FROZEN_PRIOR_CHANGES)
    cand = build_candidate_sources(allowed, orig, prior)
    raw = copy.deepcopy(FROZEN_RAW_SUBSET_CORRECTION)

    # Valid before
    assert validate_change_mode(raw["changes"][0], cand[PATH_NEW_CORR], force_full=True) == "full"
    assert rebase_correction_result(_project_to_canonical(raw), allowed, orig, cand) == FROZEN_EXPECTED_REBASED_RESULT

    # ONE mutation: replacement transport for originally-new
    mutated_change = {
        "path": PATH_NEW_CORR,
        "original_sha256": cand[PATH_NEW_CORR]["sha256"],
        "content": None,
        "replacements": [{"old": "pass", "new": "return"}],
    }

    # Mode validator rejects replacement mode under force_full
    with pytest.raises(TransportPolicyError, match="force_full does not permit replacement change mode"):
        validate_change_mode(mutated_change, cand[PATH_NEW_CORR], force_full=True)

    _assert_inputs_unchanged(allowed, orig, prior, cand, raw)

    # Valid after
    assert rebase_correction_result(_project_to_canonical(raw), allowed, orig, cand) == FROZEN_EXPECTED_REBASED_RESULT
