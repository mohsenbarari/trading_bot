"""Correction delta overlay and deterministic rebasing for two-site proposals."""
from __future__ import annotations

import hashlib
import re
from typing import Any

HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CANONICAL_RESULT_KEYS = frozenset({
    "status", "summary", "covered_requirements", "findings", "changes",
})
CANONICAL_CHANGE_KEYS = frozenset({
    "path", "original_sha256", "content",
})
SOURCE_RECORD_KEYS = frozenset({
    "sha256", "content",
})


class CorrectionDeltaError(ValueError):
    """Raised when correction delta overlay or rebasing fails validation."""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_allowed_files(allowed_files: Any) -> list[str]:
    if not isinstance(allowed_files, (list, tuple)) or not allowed_files:
        raise CorrectionDeltaError("allowed_files must be a nonempty list or tuple")
    seen = set()
    validated = []
    for f in allowed_files:
        if not isinstance(f, str) or not f.strip():
            raise CorrectionDeltaError("allowed_files entries must be nonempty strings")
        if f in seen:
            raise CorrectionDeltaError(f"duplicate allowed file: {f!r}")
        seen.add(f)
        validated.append(f)
    return validated


def _validate_source_map(sources: Any, allowed_files: list[str], label: str) -> None:
    if not isinstance(sources, dict):
        raise CorrectionDeltaError(f"{label} must be a dict")
    for path in allowed_files:
        if path not in sources:
            raise CorrectionDeltaError(f"allowed file {path!r} not bound in {label}")
    for path, record in sources.items():
        if not isinstance(path, str) or not path.strip():
            raise CorrectionDeltaError(f"invalid source path {path!r} in {label}")
        if not isinstance(record, dict) or set(record.keys()) != SOURCE_RECORD_KEYS:
            raise CorrectionDeltaError(f"invalid source record shape for {path!r} in {label}")
        sha256 = record["sha256"]
        content = record["content"]
        if sha256 is None and content is None:
            continue
        if (
            isinstance(sha256, str)
            and bool(HEX64_PATTERN.fullmatch(sha256))
            and isinstance(content, str)
        ):
            continue
        raise CorrectionDeltaError(f"invalid source record values for {path!r} in {label}")


def _validate_changes(
    changes: Any,
    allowed_files: list[str],
    baseline_sources: dict[str, dict[str, str | None]],
    label: str,
) -> list[dict[str, Any]]:
    if not isinstance(changes, list):
        raise CorrectionDeltaError(f"{label} changes must be a list")
    allowed_set = set(allowed_files)
    seen = set()
    validated = []
    for change in changes:
        if not isinstance(change, dict) or set(change.keys()) != CANONICAL_CHANGE_KEYS:
            raise CorrectionDeltaError(f"invalid canonical change shape in {label}")
        path = change["path"]
        if not isinstance(path, str) or path not in allowed_set:
            raise CorrectionDeltaError(f"change path {path!r} outside allowed files in {label}")
        if path in seen:
            raise CorrectionDeltaError(f"duplicate change path {path!r} in {label}")
        seen.add(path)

        baseline_record = baseline_sources[path]
        expected_sha = baseline_record["sha256"]
        if change["original_sha256"] != expected_sha:
            raise CorrectionDeltaError(
                f"SHA mismatch for {path!r} in {label}: expected {expected_sha!r}, "
                f"got {change['original_sha256']!r}"
            )

        content = change["content"]
        if not isinstance(content, str) or not content.strip():
            raise CorrectionDeltaError(f"change content for {path!r} in {label} must be a nonempty string")

        if content == baseline_record["content"]:
            raise CorrectionDeltaError(f"change content for {path!r} in {label} produces no byte change")

        validated.append({
            "path": path,
            "original_sha256": change["original_sha256"],
            "content": content,
        })
    return validated


def build_candidate_sources(
    allowed_files: list[str] | tuple[str, ...],
    original_sources: dict[str, dict[str, str | None]],
    prior_changes: list[dict[str, Any]],
) -> dict[str, dict[str, str | None]]:
    """Overlay prior canonical changes onto a deep logical copy of original sources."""
    valid_allowed = _validate_allowed_files(allowed_files)
    _validate_source_map(original_sources, valid_allowed, "original_sources")
    valid_changes = _validate_changes(prior_changes, valid_allowed, original_sources, "prior_changes")

    candidate_sources: dict[str, dict[str, str | None]] = {
        path: {"sha256": record["sha256"], "content": record["content"]}
        for path, record in original_sources.items()
    }

    for change in valid_changes:
        path = change["path"]
        content = change["content"]
        candidate_sources[path] = {
            "sha256": _sha256_text(content),
            "content": content,
        }

    return candidate_sources


def rebase_correction_result(
    result: dict[str, Any],
    allowed_files: list[str] | tuple[str, ...],
    original_sources: dict[str, dict[str, str | None]],
    candidate_sources: dict[str, dict[str, str | None]],
) -> dict[str, Any]:
    """Rebase a delta correction proposal into a canonical result relative to original sources."""
    valid_allowed = _validate_allowed_files(allowed_files)
    _validate_source_map(original_sources, valid_allowed, "original_sources")
    _validate_source_map(candidate_sources, valid_allowed, "candidate_sources")

    if not isinstance(result, dict) or set(result.keys()) != CANONICAL_RESULT_KEYS:
        raise CorrectionDeltaError("result must have exact canonical response keys")

    if result.get("status") != "PROPOSAL_READY":
        raise CorrectionDeltaError("result status must be PROPOSAL_READY")

    summary = result.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise CorrectionDeltaError("result summary must be a nonempty string")

    coverage = result.get("covered_requirements")
    if (
        not isinstance(coverage, list)
        or any(type(req) is not int for req in coverage)
        or len(set(coverage)) != len(coverage)
    ):
        raise CorrectionDeltaError("result covered_requirements must be a list of unique integers")

    findings = result.get("findings")
    if not isinstance(findings, list) or len(findings) != 0:
        raise CorrectionDeltaError("result findings must be an empty list for PROPOSAL_READY")

    valid_delta_changes = _validate_changes(
        result.get("changes"), valid_allowed, candidate_sources, "delta result"
    )

    delta_by_path = {c["path"]: c["content"] for c in valid_delta_changes}

    canonical_changes: list[dict[str, Any]] = []
    for path in valid_allowed:
        orig_record = original_sources[path]
        cand_record = candidate_sources[path]

        if path in delta_by_path:
            final_content = delta_by_path[path]
        else:
            final_content = cand_record["content"]

        if final_content == orig_record["content"]:
            continue

        if final_content is None:
            raise CorrectionDeltaError(f"final source for {path!r} cannot be absent")
        if not final_content.strip():
            raise CorrectionDeltaError(f"final source for {path!r} cannot be empty")

        canonical_changes.append({
            "path": path,
            "original_sha256": orig_record["sha256"],
            "content": final_content,
        })

    if not canonical_changes:
        raise CorrectionDeltaError("complete no-op rejection: rebase yielded no canonical changes")

    return {
        "status": result["status"],
        "summary": summary,
        "covered_requirements": list(coverage),
        "findings": list(findings),
        "changes": canonical_changes,
    }
