"""Read-only Git fact verification for protected main under AE03."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from scripts.two_site_maintenance_transition_git import (
        _run_git_query,
        _validate_root,
    )
except ImportError:
    from two_site_maintenance_transition_git import (  # type: ignore[no-redef]
        _run_git_query,
        _validate_root,
    )

_HEX_CHARS = frozenset("0123456789abcdef")
_BRANCH_NAME = "refs/heads/refactor/two-site-architecture"


class ProtectedMainGitFactsError(ValueError):
    """Raised when protected main Git fact verification fails."""


__all__ = [
    "ProtectedMainGitFactsError",
    "verify_protected_main_git_locked",
]


def verify_protected_main_git_locked(
    root: Path,
    *,
    prior_main_sha: str,
    approved_main_sha: str,
    prior_head: str,
    expected_head: str,
    expected_origin_main_sha: str,
    expected_candidate_sha: str,
) -> dict[str, str]:
    """Verify read-only Git facts, commit types, and ancestry for protected main."""
    for binding in (
        prior_main_sha,
        approved_main_sha,
        prior_head,
        expected_head,
        expected_origin_main_sha,
        expected_candidate_sha,
    ):
        if (
            type(binding) is not str
            or len(binding) != 40
            or not all(c in _HEX_CHARS for c in binding)
        ):
            raise ProtectedMainGitFactsError("invalid protected main git binding") from None

    try:
        validated_root = _validate_root(root)
        expected_seven = (
            (["rev-parse", "--show-toplevel"], str(validated_root).encode("utf-8") + b"\n"),
            (["symbolic-ref", "-q", "HEAD"], f"{_BRANCH_NAME}\n".encode("utf-8")),
            (["rev-parse", "--verify", "HEAD"], expected_head.encode("utf-8") + b"\n"),
            (["rev-parse", "--verify", "refs/heads/main"], approved_main_sha.encode("utf-8") + b"\n"),
            (["rev-parse", "--verify", "refs/remotes/origin/main"], expected_origin_main_sha.encode("utf-8") + b"\n"),
            (["rev-parse", "--verify", "refs/heads/candidate/wa-ir-standby-v1"], expected_candidate_sha.encode("utf-8") + b"\n"),
            (["status", "--porcelain=v1", "--untracked-files=all"], b""),
        )

        for argv, expected in expected_seven:
            out = _run_git_query(validated_root, argv)
            if type(out) is not bytes or out != expected:
                raise ProtectedMainGitFactsError("invalid protected main git facts")

        for sha in (
            prior_main_sha,
            approved_main_sha,
            prior_head,
            expected_head,
            expected_origin_main_sha,
            expected_candidate_sha,
        ):
            out = _run_git_query(validated_root, ["cat-file", "-t", sha])
            if type(out) is not bytes or out != b"commit\n":
                raise ProtectedMainGitFactsError("invalid protected main git facts")

        for left, right in (
            (prior_main_sha, approved_main_sha),
            (prior_head, expected_head),
            (approved_main_sha, expected_head),
        ):
            out = _run_git_query(validated_root, ["merge-base", "--is-ancestor", left, right])
            if type(out) is not bytes or out != b"":
                raise ProtectedMainGitFactsError("invalid protected main git facts")

        for argv, expected in expected_seven:
            out = _run_git_query(validated_root, argv)
            if type(out) is not bytes or out != expected:
                raise ProtectedMainGitFactsError("invalid protected main git facts")

    except Exception:
        raise ProtectedMainGitFactsError("invalid protected main git facts") from None

    return {
        "root": str(root),
        "branch": _BRANCH_NAME,
        "head": expected_head,
        "main": approved_main_sha,
        "origin_main": expected_origin_main_sha,
        "candidate": expected_candidate_sha,
        "status": "",
    }
