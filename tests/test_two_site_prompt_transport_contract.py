"""Contract tests for Gemini task prompt transport clauses."""
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT / "docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md"


def get_prompt_text() -> str:
    assert PROMPT_PATH.is_file(), f"missing prompt template: {PROMPT_PATH}"
    content = PROMPT_PATH.read_text(encoding="utf-8")
    assert content.strip(), "prompt template must not be empty"
    return content


def normalize_whitespace(text: str) -> str:
    """Normalize whitespace so assertions are insensitive to Markdown line wrapping."""
    return " ".join(text.split())


def parse_prompt_sections(text: str) -> dict[str, str]:
    """Parse prompt into its high-level contract sections."""
    markers = [
        ("header", "# Complete implementation"),
        ("worker", "For ROLE worker:"),
        ("verifier", "For ROLE verifier:"),
        ("correction", "For correction runs"),
        ("footer", "Return exactly the requested JSON object"),
    ]
    indices = []
    for name, marker in markers:
        idx = text.find(marker)
        assert idx != -1, f"section marker {marker!r} not found in prompt"
        indices.append((name, idx))

    sections = {}
    for i, (name, idx) in enumerate(indices):
        end_idx = indices[i + 1][1] if i + 1 < len(indices) else len(text)
        sections[name] = normalize_whitespace(text[idx:end_idx])
    return sections


def test_prompt_template_sections_parse():
    text = get_prompt_text()
    sections = parse_prompt_sections(text)
    assert "header" in sections
    assert "worker" in sections
    assert "verifier" in sections
    assert "correction" in sections
    assert "footer" in sections


def test_correction_worker_transport_clauses():
    text = get_prompt_text()
    sections = parse_prompt_sections(text)
    correction = sections["correction"]

    # Requirement 1: exact correction-candidate semantics
    assert "In correction Worker context" in correction
    assert "sources is the complete current candidate state" in correction
    assert "feedback is SHA-bound" in correction
    assert "prior_identity contains the prior run/SHA" in correction
    assert "run_id" in correction and "proposal_sha256" in correction

    # Candidate SHA use, direct change scope, and finding resolution
    assert "Change only paths needed to resolve every finding" in correction
    assert "Use candidate original_sha256 values" in correction
    assert "candidate original_sha256" in correction
    assert "resolve every" in correction.lower()
    assert "address EVERY finding in feedback" in correction

    # Omission/retention of unaffected paths and rebase
    assert "Omit unaffected paths" in correction
    assert "controller retains/rebases them" in correction

    # Prohibition on unintended reverts
    assert "Never revert accepted bytes unless a finding requires it" in correction


def test_reviewer_transport_clauses():
    text = get_prompt_text()
    sections = parse_prompt_sections(text)
    verifier = sections["verifier"]

    # Requirement 2: exact Reviewer transport fields
    assert "canonical_proposal_sha256" in verifier
    assert "changed_paths" in verifier
    assert "unified_diff" in verifier
    for meta_field in ("status", "summary", "covered_requirements", "findings"):
        assert meta_field in verifier

    # Deterministic diff against supplied original sources
    assert "deterministic unified_diff against supplied original sources" in verifier

    # Semantic reconstruction from sources+diff
    assert "Semantically review" in verifier
    assert "reconstruct" in verifier.lower() or "reconstructed from sources" in verifier
    assert "sources+diff" in verifier or "sources plus unified_diff" in verifier

    # Prohibit rejecting merely because raw replacement/canonical full content absent
    assert "raw replacement" in verifier
    assert "Do not reject merely because canonical full content or raw replacement form is absent" in verifier
    assert "without demanding duplicated canonical full-file or raw replacement representation" in verifier

    # Non-authoring Reviewer constraint
    assert "changes=[]" in verifier
    assert "changes must be an empty list" in verifier


def test_preserved_core_constraints():
    text = get_prompt_text()
    sections = parse_prompt_sections(text)

    # Requirement 3: preserve all existing invariants
    assert "no native tools" in text
    assert "`plan` mode restricts tool execution" in text
    assert "irreversibly invalidates" in text

    # Complete implementation
    assert "Solve the whole bounded task" in sections["worker"]
    assert "One response must contain finished actual implementation" in sections["worker"]

    # Fresh independent review
    assert "fresh Gemini session" in sections["verifier"]
    assert "no direct communication from Worker" in sections["verifier"]
    assert "changes must be an empty list" in sections["verifier"]

    # Canonical schema keys in footer
    assert "status, summary, covered_requirements, findings, changes" in sections["footer"]
