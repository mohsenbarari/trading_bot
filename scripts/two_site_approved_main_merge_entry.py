"""Single reviewed approved-main merge entry validator under AE03."""

from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath
from typing import Any

try:
    from scripts.two_site_maintenance_transition_record import (
        _validate_control_path,
        _validate_run_id,
    )
except ImportError:
    from two_site_maintenance_transition_record import (  # type: ignore[no-redef]
        _validate_control_path,
        _validate_run_id,
    )

MAX_APPROVED_MAIN_PATHS: int = 32
MAX_CONTROL_BLOBS: int = 256
MAX_PATH_LEN: int = 1024

_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_ENTRY_KEYS = frozenset({
    "head",
    "parents",
    "paths",
    "proposal_sha256",
    "reviewer_run_id",
    "test_evidence_sha256",
    "worker_run_id",
})


class ApprovedMainMergeEntryError(ValueError):
    """Raised when approved-main merge entry validation fails."""


__all__ = [
    "ApprovedMainMergeEntryError",
    "validate_approved_main_merge_entry",
]


def _fail() -> None:
    raise ApprovedMainMergeEntryError("invalid approved main merge entry") from None


def _validate_main_path(p: Any, control_blob_paths: set[str]) -> str:
    if (
        type(p) is not str
        or not (1 <= len(p) <= MAX_PATH_LEN)
        or p != p.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in p)
        or "\\" in p
        or p.startswith("/")
        or ":" in p
        or any(c in "*?[]{}" for c in p)
    ):
        _fail()
    segs = p.split("/")
    if any(s in (".", "..", "") for s in segs):
        _fail()
    if posixpath.normpath(p) != p or PurePosixPath(p).as_posix() != p:
        _fail()
    for s in segs:
        if s in (".git", ".local", "credentials", "secrets") or s.startswith(".env"):
            _fail()
    if p in control_blob_paths:
        _fail()
    return p


def validate_approved_main_merge_entry(
    entry: Any,
    *,
    prior_integration_head: Any,
    approved_main_sha: Any,
    control_blob_paths: Any,
) -> dict[str, Any]:
    """Validate a single approved-main merge entry and return detached copy."""
    if (
        type(prior_integration_head) is not str
        or not _HEX_40_RE.fullmatch(prior_integration_head)
        or type(approved_main_sha) is not str
        or not _HEX_40_RE.fullmatch(approved_main_sha)
        or prior_integration_head == approved_main_sha
    ):
        _fail()

    if (
        type(control_blob_paths) is not frozenset
        or len(control_blob_paths) > MAX_CONTROL_BLOBS
    ):
        _fail()

    validated_controls: set[str] = set()
    for cp in control_blob_paths:
        try:
            norm_cp = _validate_control_path(cp)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            _fail()
        validated_controls.add(norm_cp)

    if (
        type(entry) is not dict
        or set(entry.keys()) != _ENTRY_KEYS
        or len(entry) != 7
    ):
        _fail()

    head = entry.get("head")
    if (
        type(head) is not str
        or not _HEX_40_RE.fullmatch(head)
        or head == prior_integration_head
        or head == approved_main_sha
    ):
        _fail()

    parents = entry.get("parents")
    if type(parents) is not list or len(parents) != 2:
        _fail()
    p0, p1 = parents[0], parents[1]
    if (
        type(p0) is not str
        or type(p1) is not str
        or p0 != prior_integration_head
        or p1 != approved_main_sha
    ):
        _fail()

    raw_paths = entry.get("paths")
    if type(raw_paths) is not list or not (1 <= len(raw_paths) <= MAX_APPROVED_MAIN_PATHS):
        _fail()
    val_paths: list[str] = []
    seen_paths: set[str] = set()
    for p in raw_paths:
        norm_p = _validate_main_path(p, validated_controls)
        if norm_p in seen_paths:
            _fail()
        seen_paths.add(norm_p)
        val_paths.append(norm_p)
    if val_paths != sorted(val_paths):
        _fail()

    try:
        val_w_id = _validate_run_id(entry.get("worker_run_id"), "worker_run_id")
        val_r_id = _validate_run_id(entry.get("reviewer_run_id"), "reviewer_run_id")
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        _fail()
    if val_w_id == val_r_id:
        _fail()

    prop_sha = entry.get("proposal_sha256")
    if type(prop_sha) is not str or not _HEX_64_RE.fullmatch(prop_sha):
        _fail()

    ev_sha = entry.get("test_evidence_sha256")
    if type(ev_sha) is not str or not _HEX_64_RE.fullmatch(ev_sha):
        _fail()

    return {
        "head": head,
        "parents": [p0, p1],
        "paths": list(val_paths),
        "proposal_sha256": prop_sha,
        "reviewer_run_id": val_r_id,
        "test_evidence_sha256": ev_sha,
        "worker_run_id": val_w_id,
    }
