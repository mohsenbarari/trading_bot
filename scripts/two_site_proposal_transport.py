#!/usr/bin/env python3
"""Minimal pure transport policy for two-site proposal automation."""
from __future__ import annotations

import re

FULL_CONTENT_BYTE_THRESHOLD = 8192
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TransportPolicyError(ValueError):
    """Raised when proposal transport policy or contract validation fails."""


def _check_positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise TransportPolicyError(f"{name} must be a positive integer")
    return value


def _validate_source_record(source_record: object) -> None:
    if not isinstance(source_record, dict) or set(source_record.keys()) != {"sha256", "content"}:
        raise TransportPolicyError("source_record must be a dict with exact keys {'sha256', 'content'}")
    sha = source_record["sha256"]
    content = source_record["content"]
    if sha is None and content is None:
        return
    if isinstance(sha, str) and isinstance(content, str):
        if not _SHA256_RE.fullmatch(sha):
            raise TransportPolicyError(f"invalid SHA syntax: {sha!r}")
        return
    raise TransportPolicyError("malformed source record {sha256, content} values")


def classify_source(
    source_record: object,
    threshold: int = FULL_CONTENT_BYTE_THRESHOLD,
) -> str:
    _check_positive_int(threshold, "threshold")
    _validate_source_record(source_record)
    assert isinstance(source_record, dict)
    if source_record["content"] is None:
        return "new"
    content_bytes = source_record["content"].encode("utf-8")
    if len(content_bytes) >= threshold:
        return "large"
    return "small"


def _make_four_key_branch(
    path: str,
    sha: str | None,
    content_schema: dict,
    replacements_schema: dict,
) -> dict:
    sha_schema = {"type": "null"} if sha is None else {"enum": [sha]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "original_sha256", "content", "replacements"],
        "properties": {
            "path": {"enum": [path]},
            "original_sha256": sha_schema,
            "content": content_schema,
            "replacements": replacements_schema,
        },
    }


def _full_mode_branch(path: str, sha: str | None) -> dict:
    return _make_four_key_branch(
        path,
        sha,
        content_schema={"type": "string"},
        replacements_schema={"type": "array", "maxItems": 0},
    )


def _replacement_mode_branch(path: str, sha: str | None, max_replacements: int) -> dict:
    return _make_four_key_branch(
        path,
        sha,
        content_schema={"type": "null"},
        replacements_schema={
            "type": "array",
            "minItems": 1,
            "maxItems": max_replacements,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["old", "new"],
                "properties": {
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                },
            },
        },
    )


def build_changes_schema(
    role: str,
    allowed_files: object,
    sources: object,
    max_replacements: int = 32,
    threshold: int = FULL_CONTENT_BYTE_THRESHOLD,
) -> dict:
    if role not in ("worker", "verifier"):
        raise TransportPolicyError(f"invalid role: {role!r}")
    _check_positive_int(max_replacements, "max_replacements")
    _check_positive_int(threshold, "threshold")

    if not isinstance(allowed_files, list) or not allowed_files:
        raise TransportPolicyError("allowed_files must be a nonempty list")
    if any(not isinstance(f, str) or not f for f in allowed_files):
        raise TransportPolicyError("allowed_files must contain nonempty strings")
    if len(set(allowed_files)) != len(allowed_files):
        raise TransportPolicyError("allowed_files must not contain duplicate paths")

    if not isinstance(sources, dict):
        raise TransportPolicyError("sources must be a dict")
    for name in allowed_files:
        if name not in sources:
            raise TransportPolicyError(f"missing allowed source binding: {name}")
    for name, record in sources.items():
        if not isinstance(name, str):
            raise TransportPolicyError("source keys must be strings")
        _validate_source_record(record)

    if role == "verifier":
        return {
            "type": "array",
            "maxItems": 0,
            "items": {"type": "object", "additionalProperties": False},
        }

    branches: list[dict] = []
    for path in allowed_files:
        rec = sources[path]
        kind = classify_source(rec, threshold=threshold)
        sha = rec["sha256"]
        if kind == "new":
            branches.append(_full_mode_branch(path, sha))
        elif kind == "large":
            branches.append(_replacement_mode_branch(path, sha, max_replacements))
        elif kind == "small":
            branches.append(_full_mode_branch(path, sha))
            branches.append(_replacement_mode_branch(path, sha, max_replacements))

    return {
        "type": "array",
        "items": {
            "anyOf": branches,
        },
    }


def validate_change_mode(
    change: object,
    source_record: object,
    max_replacements: int = 32,
    threshold: int = FULL_CONTENT_BYTE_THRESHOLD,
) -> str:
    _check_positive_int(max_replacements, "max_replacements")
    _check_positive_int(threshold, "threshold")
    source_kind = classify_source(source_record, threshold=threshold)

    if not isinstance(change, dict):
        raise TransportPolicyError("change must be a dict")

    keys = set(change.keys())
    if keys == {"path", "original_sha256", "content"}:
        mode = "legacy"
        if not isinstance(change["content"], str):
            raise TransportPolicyError("legacy change content must be a string")
    elif keys == {"path", "original_sha256", "content", "replacements"}:
        content = change["content"]
        replacements = change["replacements"]
        if not (isinstance(content, str) or content is None) or not isinstance(replacements, list):
            raise TransportPolicyError("invalid change content or replacements type")
        if isinstance(content, str) and len(replacements) == 0:
            mode = "full"
        elif content is None and len(replacements) > 0:
            if len(replacements) > max_replacements:
                raise TransportPolicyError(f"replacements count {len(replacements)} exceeds {max_replacements}")
            mode = "replacement"
        elif isinstance(content, str) and len(replacements) > 0:
            raise TransportPolicyError("mixed change content and replacements")
        else:
            raise TransportPolicyError("empty change content and replacements")
    else:
        raise TransportPolicyError("invalid change shape")

    if not isinstance(change["path"], str) or not change["path"]:
        raise TransportPolicyError("change path must be a nonempty string")
    if change["original_sha256"] is not None and not isinstance(change["original_sha256"], str):
        raise TransportPolicyError("change original_sha256 must be a string or None")
    assert isinstance(source_record, dict)
    if change["original_sha256"] != source_record["sha256"]:
        raise TransportPolicyError("change original_sha256 does not match source record")

    if source_kind == "new":
        if mode != "full":
            raise TransportPolicyError(f"new file does not permit {mode} change mode")
    elif source_kind == "large":
        if mode != "replacement":
            raise TransportPolicyError(f"large file does not permit {mode} change mode")
    elif source_kind == "small":
        pass

    return mode
