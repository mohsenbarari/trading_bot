"""Optional immutable task requirement for consolidated proposal byte feedback.

This grammar grants no execution, source, retry or acceptance authority. Tasks
without a starting-prefix declaration keep their legacy byte-budget behavior.
"""
from __future__ import annotations

import json

try:
    from scripts.two_site_agent_sandbox import _validate_overlay_path
except ImportError:
    from two_site_agent_sandbox import _validate_overlay_path

PREFIX = "PROPOSAL_UTF8_BUDGET_V1:"
ERROR = "invalid proposal UTF-8 budget profile"
MAX_BODY_BYTES, MAX_PATH_BYTES, MAX_PATHS, MAX_LIMIT = 8192, 128, 32, 160000


class ProposalByteProfileError(ValueError):
    """Invalid explicitly declared profile (never contains untrusted metadata)."""


def _unique_json(body: str):
    # Lazy import: the runner imports the receipt module, which imports us.
    try:
        from scripts.two_site_proposal_runner import unique_json
    except ImportError:
        from two_site_proposal_runner import unique_json
    return unique_json(body)


def _limit(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_LIMIT


def _paths(value: object) -> list[str]:
    if type(value) is not list or not 1 <= len(value) <= MAX_PATHS:
        raise ValueError
    if any(type(path) is not str or len(path) > MAX_PATH_BYTES for path in value):
        raise ValueError
    if len(set(value)) != len(value):
        raise ValueError
    return value


def parse_proposal_utf8_budget_profile(
    requirements: object, allowed_files: object, changed_paths: object,
) -> dict | None:
    """Parse one canonical declaration; return a detached map or legacy None.

    Only top-level strings are inspected. Non-list reserved declarations fail
    closed; non-declarations do not inspect allowed_files or changed_paths.
    Task requirement containers are bounded to the existing 32-entry contract.
    """
    try:
        if isinstance(requirements, str):
            entries = (requirements,)
        elif isinstance(requirements, (list, tuple)):
            size = list.__len__(requirements) if isinstance(requirements, list) else tuple.__len__(requirements)
            if size > MAX_PATHS:
                raise ValueError
            entries = list.__iter__(requirements) if isinstance(requirements, list) else tuple.__iter__(requirements)
        else:
            return None
        declaration = None
        for item in entries:
            if isinstance(item, str) and str.startswith(item, PREFIX):
                if type(requirements) is not list or type(item) is not str or declaration is not None:
                    raise ValueError
                declaration = item
        if declaration is None:
            return None
        if len(declaration) - len(PREFIX) > MAX_BODY_BYTES:
            raise ValueError
        body = declaration[len(PREFIX):]
        if not body.isascii():
            raise ValueError
        profile = _unique_json(body)
        if type(profile) is not dict or set(profile) != {"version", "file_limits", "total_limit"}:
            raise ValueError
        if type(profile["version"]) is not str or profile["version"] != "1.0":
            raise ValueError
        limits = profile["file_limits"]
        if type(limits) is not dict or not 1 <= len(limits) <= MAX_PATHS:
            raise ValueError
        if not _limit(profile["total_limit"]) or any(not _limit(cap) for cap in limits.values()):
            raise ValueError
        if json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) != body:
            raise ValueError
        allowed, changed = _paths(allowed_files), _paths(changed_paths)
        if set(limits) != set(allowed) or not set(changed) <= set(allowed):
            raise ValueError
        for path in allowed:
            if len(path) > MAX_PATH_BYTES or len(path.encode("utf-8")) > MAX_PATH_BYTES:
                raise ValueError
            if any(c in path for c in "*?[]{}"):
                raise ValueError
            _validate_overlay_path(path)
        return {"version": "1.0", "file_limits": dict(sorted(limits.items())), "total_limit": profile["total_limit"]}
    except Exception:
        raise ProposalByteProfileError(ERROR) from None


def require_proposal_utf8_budget_profile(requirements: object, allowed_files: object) -> dict:
    """Require an explicit profile for newly prepared work; legacy parser stays optional."""
    profile = parse_proposal_utf8_budget_profile(requirements, allowed_files, allowed_files)
    if profile is None:
        raise ProposalByteProfileError(ERROR)
    return profile
