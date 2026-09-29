"""Compact review context builder for single-file review prompt material."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PureWindowsPath
from typing import Any

from scripts import two_site_proposal_runner as runner
from scripts.two_site_source_excerpts import extract_bundle

HEX_DIGITS = set("0123456789abcdef")


class CompactReviewError(ValueError):
    """Raised when compact review material construction or validation fails."""


COMPACT_REVIEW_MARKER = "\nCOMPACT_REVIEW_SYMBOLS_JSON:\n"
CANONICAL_EXCERPTS_MARKER = "\nCANONICAL_SOURCE_EXCERPTS_JSON:"
MAX_COMPACT_PAYLOAD_BYTES = 4096


def parse_one_file_compact_review_spec(goal: str) -> tuple[str, list[str]] | None:
    """Parse pure opt-in compact review path and symbol list from task goal."""
    if not isinstance(goal, str):
        raise CompactReviewError("goal must be str")

    if COMPACT_REVIEW_MARKER not in goal:
        return None

    if goal.count(COMPACT_REVIEW_MARKER) > 1 or goal.count("\nCOMPACT_REVIEW_SYMBOLS_JSON:") > 1:
        raise CompactReviewError("duplicate compact review marker")

    marker_idx = goal.find(COMPACT_REVIEW_MARKER)
    tail = goal[marker_idx + len(COMPACT_REVIEW_MARKER):]

    can_idx = tail.find(CANONICAL_EXCERPTS_MARKER)
    if can_idx != -1:
        json_text = tail[:can_idx]
    else:
        json_text = tail

    payload_str = json_text.rstrip("\r\n")
    if len(payload_str.encode("utf-8")) > MAX_COMPACT_PAYLOAD_BYTES:
        raise CompactReviewError("compact review JSON payload exceeds 4096 UTF-8 bytes")

    def _check_pairs(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
        keys: set[str] = set()
        res: dict[str, Any] = {}
        for k, v in pairs:
            if k in keys:
                raise CompactReviewError(f"duplicate key in compact review JSON: {k}")
            keys.add(k)
            res[k] = v
        return res

    try:
        data = json.loads(payload_str, object_pairs_hook=_check_pairs)
    except CompactReviewError:
        raise
    except Exception as exc:
        raise CompactReviewError(f"malformed or trailing JSON in compact review payload: {exc}") from exc

    if not isinstance(data, dict):
        raise CompactReviewError("compact review payload must be a JSON object")

    if set(data.keys()) != {"path", "symbols"}:
        raise CompactReviewError("compact review payload must have exactly keys 'path' and 'symbols'")

    path = data["path"]
    if not isinstance(path, str):
        raise CompactReviewError("path must be a string")

    if not path.endswith(".py"):
        raise CompactReviewError("path must end with .py")

    if "\\" in path or "\x00" in path:
        raise CompactReviewError("path contains backslash or NUL byte")

    if PureWindowsPath(path).drive or (len(path) >= 2 and path[1] == ":" and path[0].isalpha()):
        raise CompactReviewError("path must not be drive-prefixed")

    if path.startswith("/") or Path(path).is_absolute():
        raise CompactReviewError("path must not be absolute")

    for seg in path.split("/"):
        if not seg or seg == "." or seg == "..":
            raise CompactReviewError("path contains empty, dot, or dotdot segments")

    symbols = data["symbols"]
    if not isinstance(symbols, list):
        raise CompactReviewError("symbols must be a list")

    if not symbols:
        raise CompactReviewError("symbols must not be empty")

    for s in symbols:
        if not isinstance(s, str) or not s.isidentifier():
            raise CompactReviewError("symbol must be an identifier string")

    if len(set(symbols)) != len(symbols):
        raise CompactReviewError("symbols must not contain duplicates")

    return (path, list(symbols))


def build_one_file_compact_review_material(
    root: Path | str,
    task: dict[str, Any],
    sources: dict[str, Any],
    review_transport: dict[str, Any],
    test_receipt: dict[str, Any],
    source_head: str,
    path: str,
    symbols: list[str],
) -> dict[str, Any]:
    """Compose single-file AST excerpt review material within budget."""
    if not isinstance(source_head, str) or not source_head:
        raise CompactReviewError("invalid source_head")

    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise CompactReviewError(f"root not directory: {root}")

    try:
        current_head = runner.git(root_path, "rev-parse", "HEAD").strip()
    except Exception as exc:
        raise CompactReviewError(f"git rev-parse failed: {exc}") from exc

    if current_head != source_head:
        raise CompactReviewError(f"HEAD mismatch: expected {source_head}, got {current_head}")

    if not isinstance(path, str) or not path.endswith(".py"):
        raise CompactReviewError("path must be .py")

    if not isinstance(review_transport, dict):
        raise CompactReviewError("invalid review_transport")

    changed = review_transport.get("changed_paths")
    if not isinstance(changed, (list, tuple)) or not changed:
        raise CompactReviewError("invalid changed_paths")

    for p in changed:
        if not isinstance(p, str) or not p:
            raise CompactReviewError("invalid changed path item")

    if len(set(changed)) != len(changed) or path not in changed:
        raise CompactReviewError("changed_paths duplicate or missing path")

    if not isinstance(task, dict):
        raise CompactReviewError("invalid task")

    allowed = task.get("allowed_files")
    if not isinstance(allowed, (list, tuple, set)) or path not in allowed:
        raise CompactReviewError("path not in task allowed_files")

    if not isinstance(sources, dict) or path not in sources:
        raise CompactReviewError("path missing from sources")

    entry = sources[path]
    if not isinstance(entry, dict):
        raise CompactReviewError("source entry must be dict")

    content = entry.get("content")
    if not isinstance(content, str):
        raise CompactReviewError("source content must be str")

    raw_bytes = content.encode("utf-8")
    if len(raw_bytes) < 8192:
        raise CompactReviewError("source content under 8192 bytes")

    sha256_val = entry.get("sha256")
    if (
        not isinstance(sha256_val, str)
        or len(sha256_val) != 64
        or any(c not in HEX_DIGITS for c in sha256_val)
        or hashlib.sha256(raw_bytes).hexdigest() != sha256_val
    ):
        raise CompactReviewError("source sha256 mismatch or invalid hex")

    if not isinstance(symbols, (list, tuple)) or not symbols:
        raise CompactReviewError("invalid symbols")

    for s in symbols:
        if not isinstance(s, str) or not s.isidentifier():
            raise CompactReviewError("symbol must be identifier string")

    symbol_list = list(symbols)
    if len(set(symbol_list)) != len(symbol_list):
        raise CompactReviewError("symbols must be unique")

    try:
        records, _ = extract_bundle(root_path, [{"path": path, "symbols": symbol_list}])
    except Exception as exc:
        raise CompactReviewError(f"extract_bundle failed: {exc}") from exc

    if not isinstance(records, list) or len(records) != 1:
        raise CompactReviewError("extract_bundle must return exactly one record")

    rec = records[0]
    if rec.get("path") != path or rec.get("symbols") != symbol_list or rec.get("sha256") != sha256_val:
        raise CompactReviewError("extracted record mismatch")

    material = {
        "task": task,
        "source_head": source_head,
        "source_manifest": {
            p: s.get("sha256") if isinstance(s, dict) else None
            for p, s in sources.items()
        },
        "exact_original_source_excerpt": rec,
        "other_original_sources": {p: s for p, s in sources.items() if p != path},
        "proposal": review_transport,
        "pre_review_test_evidence": test_receipt,
        "scope_marker": "EXCERPT_NOT_FULL_SOURCE_CODEX_FULL_REVIEW_REQUIRED",
    }

    serialized = json.dumps(material, ensure_ascii=False, sort_keys=True)
    if runner.SECRET.search(serialized):
        raise CompactReviewError("secret detected")

    if len(serialized.encode("utf-8")) > 120000:
        raise CompactReviewError("material exceeds 120000 bytes")

    return material
