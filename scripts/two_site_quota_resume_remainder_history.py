"""History loader for prior normalized reviewer findings in two-site proposal pipeline."""
from __future__ import annotations

from pathlib import Path

try:
    from scripts.two_site_proposal_pipeline import (
        RUN_PREFIX_RE,
        SHA_PATTERN,
        normalize_finding,
    )
except ImportError:
    from two_site_proposal_pipeline import (
        RUN_PREFIX_RE,
        SHA_PATTERN,
        normalize_finding,
    )

try:
    from scripts.two_site_proposal_runner import (
        MAX_CONTEXT,
        ProposalError,
        bounded_file_bytes,
        validate_correction_feedback,
    )
except ImportError:
    from two_site_proposal_runner import (
        MAX_CONTEXT,
        ProposalError,
        bounded_file_bytes,
        validate_correction_feedback,
    )

__all__ = ["load_prior_normalized_findings"]


def load_prior_normalized_findings(
    root: Path,
    original_run_prefix: str,
    correction_count: int,
    prior_worker_shas: list[str],
) -> set[str]:
    """Read and normalize prior gemini reviewer findings across correction steps."""
    if not isinstance(root, Path):
        raise ProposalError("root must be a Path")
    try:
        canonical_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProposalError(f"root must exist: {exc}") from exc
    if not canonical_root.is_dir():
        raise ProposalError(f"root must be a directory: {root}")

    if not isinstance(original_run_prefix, str) or not RUN_PREFIX_RE.fullmatch(original_run_prefix):
        raise ProposalError(f"invalid run prefix: {original_run_prefix!r}")

    if type(correction_count) is not int:
        raise ProposalError("correction_count must be a built-in integer")
    if correction_count not in (0, 1, 2):
        raise ProposalError(f"invalid correction_count: {correction_count}")

    if not isinstance(prior_worker_shas, list):
        raise ProposalError("prior_worker_shas must be a list")
    if len(prior_worker_shas) != correction_count:
        raise ProposalError("prior_worker_shas length must equal correction_count")
    for sha in prior_worker_shas:
        if not isinstance(sha, str) or not SHA_PATTERN.fullmatch(sha):
            raise ProposalError(f"invalid prior worker sha: {sha!r}")

    seen_findings: set[str] = set()
    for j in range(1, correction_count + 1):
        rel_path = (
            Path(".local")
            / "two-site-refactor"
            / "pipeline"
            / original_run_prefix
            / f"feedback-correction-{j}.json"
        )
        label = f"feedback-correction-{j}.json"
        raw_bytes = bounded_file_bytes(canonical_root, rel_path, MAX_CONTEXT, label)
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProposalError(f"{label} is not valid UTF-8: {exc}") from exc

        feedback = validate_correction_feedback(text, prior_worker_shas[j - 1])
        reviewer = feedback.get("reviewer")
        if reviewer == "gemini":
            for finding in feedback["findings"]:
                norm = normalize_finding(finding)
                if norm in seen_findings:
                    raise ProposalError(f"duplicate normalized finding: {finding}")
                seen_findings.add(norm)
        elif reviewer == "test-gate":
            continue
        else:
            raise ProposalError(f"rejected reviewer in correction feedback: {reviewer}")

    return seen_findings
