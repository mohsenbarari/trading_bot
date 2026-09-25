"""Hermetic smoke tests for load_prior_normalized_findings."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import pytest

try:
    from scripts.two_site_proposal_pipeline import RUN_PREFIX_RE
    from scripts.two_site_proposal_runner import ProposalError
    from scripts.two_site_quota_resume_remainder_history import (
        load_prior_normalized_findings,
    )
except ImportError:
    from two_site_proposal_pipeline import RUN_PREFIX_RE
    from two_site_proposal_runner import ProposalError
    from two_site_quota_resume_remainder_history import (
        load_prior_normalized_findings,
    )


def _valid_prefix() -> str:
    for candidate in ("run-20260925-001", "20260925T100000Z-abcdef12", "run-1", "run_1"):
        if RUN_PREFIX_RE.fullmatch(candidate):
            return candidate
    raise AssertionError(f"No valid prefix matched {RUN_PREFIX_RE.pattern}")


def test_count_zero_returns_empty_set():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        prefix = _valid_prefix()
        assert load_prior_normalized_findings(root, prefix, 0, []) == set()


def test_gemini_findings_normalized_correctly():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        prefix = _valid_prefix()
        sha1 = "a" * 64
        dir_path = root / ".local" / "two-site-refactor" / "pipeline" / prefix
        dir_path.mkdir(parents=True)
        payload = {
            "reviewer": "gemini",
            "reviewed_proposal_sha256": sha1,
            "findings": ["  First Finding  ", "second   finding"],
            "test_evidence": ["evidence 1"],
        }
        (dir_path / "feedback-correction-1.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        result = load_prior_normalized_findings(root, prefix, 1, [sha1])
        assert result == {"first finding", "second finding"}


def test_test_gate_ignored_and_codex_or_wrong_sha_rejected():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        prefix = _valid_prefix()
        sha1 = "a" * 64
        sha2 = "b" * 64
        dir_path = root / ".local" / "two-site-refactor" / "pipeline" / prefix
        dir_path.mkdir(parents=True)

        payload_gate = {
            "reviewer": "test-gate",
            "reviewed_proposal_sha256": sha1,
            "findings": ["gate issue"],
            "test_evidence": ["test failed"],
        }
        (dir_path / "feedback-correction-1.json").write_text(
            json.dumps(payload_gate), encoding="utf-8"
        )
        assert load_prior_normalized_findings(root, prefix, 1, [sha1]) == set()

        payload_codex = {
            "reviewer": "codex",
            "reviewed_proposal_sha256": sha2,
            "findings": ["codex issue"],
            "test_evidence": [],
        }
        (dir_path / "feedback-correction-2.json").write_text(
            json.dumps(payload_codex), encoding="utf-8"
        )
        with pytest.raises(ProposalError):
            load_prior_normalized_findings(root, prefix, 2, [sha1, sha2])

        with pytest.raises(ProposalError):
            load_prior_normalized_findings(root, prefix, 1, ["c" * 64])
