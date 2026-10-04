"""Contiguous pre/main/post sequence normalizer under AE03."""

from __future__ import annotations

import copy
import re
from typing import Any

try:
    from scripts.two_site_maintenance_transition_record import (
        MAX_MAINTENANCE_COMMITS,
        _validate_control_path,
        _validate_maintenance_commits,
    )
except ImportError:
    from two_site_maintenance_transition_record import (  # type: ignore[no-redef]
        MAX_MAINTENANCE_COMMITS,
        _validate_control_path,
        _validate_maintenance_commits,
    )

try:
    from scripts.two_site_approved_main_merge_entry import (
        validate_approved_main_merge_entry,
    )
except ImportError:
    from two_site_approved_main_merge_entry import (  # type: ignore[no-redef]
        validate_approved_main_merge_entry,
    )

MAX_CONTROL_BLOBS: int = 256
MAX_SEQUENCE_COMMITS: int = 32

_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")


class MainTransitionSequenceError(ValueError):
    """Raised when main transition sequence validation fails."""


__all__ = [
    "MainTransitionSequenceError",
    "validate_main_transition_sequence",
]


def _fail() -> None:
    raise MainTransitionSequenceError("invalid main transition sequence") from None


def _process_commits_part(
    commits: list[Any],
    start_endpoint: str,
    ctrl_set: set[str],
) -> tuple[list[dict[str, Any]], str]:
    if not commits:
        return [], start_endpoint
    normalized: list[dict[str, Any]] = []
    current_endpoint = start_endpoint
    for i in range(0, len(commits), MAX_MAINTENANCE_COMMITS):
        chunk = commits[i : i + MAX_MAINTENANCE_COMMITS]
        last = chunk[-1]
        if type(last) is not dict:
            _fail()
        chunk_final_head = last.get("head")
        if type(chunk_final_head) is not str or not _HEX_40_RE.fullmatch(chunk_final_head):
            _fail()
        try:
            norm_chunk = _validate_maintenance_commits(
                chunk,
                prior_head=current_endpoint,
                new_repo_head=chunk_final_head,
                control_blob_paths=ctrl_set,
            )
        except Exception:
            _fail()
        normalized.extend(norm_chunk)
        current_endpoint = chunk_final_head
    return normalized, current_endpoint


def validate_main_transition_sequence(
    pre_main_commits: Any,
    main_integration: Any,
    post_main_commits: Any,
    *,
    prior_head: Any,
    new_head: Any,
    approved_main_sha: Any,
    control_blob_paths: Any,
) -> dict[str, Any]:
    """Validate contiguous pre/main/post sequence and return detached copy."""
    if (
        type(prior_head) is not str
        or not _HEX_40_RE.fullmatch(prior_head)
        or type(new_head) is not str
        or not _HEX_40_RE.fullmatch(new_head)
        or type(approved_main_sha) is not str
        or not _HEX_40_RE.fullmatch(approved_main_sha)
        or len({prior_head, new_head, approved_main_sha}) != 3
    ):
        _fail()

    if (
        type(control_blob_paths) is not frozenset
        or len(control_blob_paths) > MAX_CONTROL_BLOBS
    ):
        _fail()

    if (
        type(pre_main_commits) is not list
        or type(post_main_commits) is not list
        or len(pre_main_commits) + 1 + len(post_main_commits) > MAX_SEQUENCE_COMMITS
    ):
        _fail()

    validated_controls: set[str] = set()
    for cp in control_blob_paths:
        try:
            norm_cp = _validate_control_path(cp)
        except Exception:
            _fail()
        validated_controls.add(norm_cp)

    normalized_pre, pre_endpoint = _process_commits_part(
        pre_main_commits, prior_head, validated_controls
    )

    try:
        norm_main = validate_approved_main_merge_entry(
            main_integration,
            prior_integration_head=pre_endpoint,
            approved_main_sha=approved_main_sha,
            control_blob_paths=control_blob_paths,
        )
    except Exception:
        _fail()

    normalized_post, post_endpoint = _process_commits_part(
        post_main_commits, norm_main["head"], validated_controls
    )

    if post_endpoint != new_head:
        _fail()

    all_heads = (
        [c["head"] for c in normalized_pre]
        + [norm_main["head"]]
        + [c["head"] for c in normalized_post]
    )
    seen_heads: set[str] = set()
    for h in all_heads:
        if h in seen_heads or h == prior_head or h == approved_main_sha:
            _fail()
        seen_heads.add(h)

    return copy.deepcopy({
        "main_integration": norm_main,
        "post_main_commits": normalized_post,
        "pre_main_commits": normalized_pre,
    })
