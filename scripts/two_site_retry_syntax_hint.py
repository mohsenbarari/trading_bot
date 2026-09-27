"""Pure bounded syntax hint extraction and validation."""

from __future__ import annotations

import json
import os
from collections.abc import Collection
from typing import Any

PREFIX = "generated Python syntax is invalid: "
CODE_SYNTAX_ERROR = "PYTHON_SYNTAX_ERROR"
EXPECTED_KEYS = {"code", "path", "line", "column"}


def _is_valid_path(path: Any, allowed_files: Collection[str]) -> bool:
    if not isinstance(path, str) or not path.endswith(".py") or path == ".py":
        return False
    if path.startswith(("/", "\\")) or os.path.isabs(path) or (len(path) >= 2 and path[1] == ":"):
        return False
    parts = path.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    try:
        return allowed_files is not None and path in allowed_files
    except (TypeError, ValueError):
        return False


def extract_syntax_hint(cause_text: str, allowed_files: Collection[str]) -> dict[str, str | int] | None:
    try:
        if not isinstance(cause_text, str) or allowed_files is None:
            return None
        if len(cause_text.encode("utf-8")) >= 512 or "\n" in cause_text or "\r" in cause_text:
            return None
        if not cause_text.startswith(PREFIX):
            return None
        parts = cause_text[len(PREFIX):].split(":", 3)
        if len(parts) != 4 or not parts[3].startswith(" ") or not parts[3][1:].strip():
            return None
        path, line_str, col_str = parts[0], parts[1], parts[2]
        if not _is_valid_path(path, allowed_files):
            return None
        if not line_str.isdigit() or (len(line_str) > 1 and line_str.startswith("0")):
            return None
        if not col_str.isdigit() or (len(col_str) > 1 and col_str.startswith("0")):
            return None
        line, col = int(line_str), int(col_str)
        if not (1 <= line <= 100000 and 1 <= col <= 10000):
            return None
        return {"code": CODE_SYNTAX_ERROR, "path": path, "line": line, "column": col}
    except Exception:
        return None


def validate_retry_hint(hint: object, allowed_files: Collection[str]) -> dict[str, str | int]:
    if not isinstance(hint, dict) or set(hint.keys()) != EXPECTED_KEYS:
        raise ValueError("hint must be a dict with exact four keys")
    if hint.get("code") != CODE_SYNTAX_ERROR or type(hint["code"]) is not str:
        raise ValueError("invalid code")
    path = hint["path"]
    if not _is_valid_path(path, allowed_files):
        raise ValueError("invalid path")
    line = hint["line"]
    if type(line) is not int or not (1 <= line <= 100000):
        raise ValueError("invalid line")
    col = hint["column"]
    if type(col) is not int or not (1 <= col <= 10000):
        raise ValueError("invalid column")
    canonical: dict[str, str | int] = {"code": CODE_SYNTAX_ERROR, "path": path, "line": line, "column": col}
    if len(json.dumps(canonical).encode("utf-8")) > 512:
        raise ValueError("serialized hint exceeds 512 bytes")
    return canonical
