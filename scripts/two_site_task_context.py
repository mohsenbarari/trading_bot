"""Advisory preparation notes helper for two-server task contexts."""
import ast
import json
import re
import unicodedata

BLOCKER_ID_RE = re.compile(r"\b(P1-\d{2}-HIGH-[A-Z0-9]+(?:-[A-Z0-9]+)*|DR-\d+)\b")
BLOCKER_REQ_RE = re.compile(
    r"\b(?:exact\s+blocker|canonical\s+blocker|exact\s+canonical\s+blocker|blocker\s+(?:is\s+)?requested|request(?:ed|ing)?\s+blocker)\b",
    re.IGNORECASE,
)


def _is_test_path(path: str) -> bool:
    if not isinstance(path, str):
        return False
    parts = path.replace("\\", "/").strip().split("/")
    return any(p in ("tests", "test") for p in parts[:-1]) or (
        parts[-1].startswith("test_") or parts[-1].endswith("_test.py")
    )


def _is_blocker_requested(task) -> bool:
    if not isinstance(task, dict):
        return False
    if any(task.get(k) for k in ("exact_blocker", "canonical_blocker", "request_blocker", "blocker_requested")):
        return True
    goal, reqs = task.get("goal", ""), task.get("requirements", [])
    texts = [goal] + (reqs if isinstance(reqs, list) else [reqs])
    return any(isinstance(t, str) and BLOCKER_REQ_RE.search(t) for t in texts)


def _extract_excerpts(task):
    if not isinstance(task, dict):
        return []
    goal, marker = task.get("goal"), "CANONICAL_SOURCE_EXCERPTS_JSON:\n"
    if isinstance(goal, str) and marker in goal:
        try:
            payload, _ = json.JSONDecoder().raw_decode(goal.split(marker, 1)[1].lstrip())
            if isinstance(payload, dict) and isinstance(payload.get("excerpts"), list):
                return payload["excerpts"]
        except Exception:
            pass
    ex = task.get("excerpts")
    return ex if isinstance(ex, list) else []


def _iter_ast_literals(tree):
    stack = [(tree, False)]
    while stack:
        curr, in_f = stack.pop()
        if isinstance(curr, ast.JoinedStr):
            for child in reversed(curr.values):
                stack.append((child, True))
        elif isinstance(curr, ast.Constant) and isinstance(curr.value, str):
            yield curr, in_f
        else:
            for child in reversed(list(ast.iter_child_nodes(curr))):
                stack.append((child, in_f))


def preparation_notes(task, sources):
    """Generate deterministic advisory notes from task context and sources."""
    warnings, exact_blocker_ids, seen_blockers = [], [], set()

    def _add_warning(warn: str):
        if warn not in warnings:
            warnings.append(warn)

    def _add_blockers(text):
        if not isinstance(text, str):
            return
        limit = 160000
        for m in BLOCKER_ID_RE.finditer(text, 0, limit):
            bid = m.group(1)
            if len(bid) <= 128 and bid not in seen_blockers:
                if len(exact_blocker_ids) >= 32:
                    _add_warning("BLOCKER_IDS_OMITTED")
                else:
                    seen_blockers.add(bid)
                    exact_blocker_ids.append(bid)
        if len(text) > limit and BLOCKER_ID_RE.search(text, limit):
            _add_warning("BLOCKER_IDS_OMITTED")

    if isinstance(task, dict):
        _add_blockers(task.get("goal"))
        reqs = task.get("requirements")
        for r in (reqs if isinstance(reqs, list) else [reqs]):
            _add_blockers(r)

    sorted_sources = []
    if isinstance(sources, dict):
        sorted_sources = sorted(sources.items(), key=lambda item: str(item[0]))
        for _, item in sorted_sources:
            _add_blockers(item.get("content") if isinstance(item, dict) else item)

    raw_excerpts = _extract_excerpts(task)
    for exc in raw_excerpts:
        if isinstance(exc, dict) and isinstance(exc.get("ranges"), list):
            for rng in exc["ranges"]:
                if isinstance(rng, dict):
                    _add_blockers(rng.get("text"))

    if _is_blocker_requested(task) and not exact_blocker_ids:
        _add_warning("MISSING_EXACT_BLOCKER_ID")

    blocks = []
    if isinstance(sources, dict):
        for path, item in sorted_sources:
            if not isinstance(path, str) or not path.endswith(".py") or _is_test_path(path):
                continue
            content = item.get("content") if isinstance(item, dict) else item
            if content is None:
                continue
            if not isinstance(content, str):
                _add_warning("SOURCE_BLOCK_OMITTED")
                continue
            blocks.append((path, 1, content))

    if len(raw_excerpts) > 32:
        _add_warning("SOURCE_BLOCK_OMITTED")

    for exc in raw_excerpts[:32]:
        if not isinstance(exc, dict):
            _add_warning("SOURCE_BLOCK_OMITTED")
            continue
        epath = exc.get("path")
        if not isinstance(epath, str) or not epath.endswith(".py") or _is_test_path(epath):
            continue
        ranges = exc.get("ranges")
        if not isinstance(ranges, list):
            _add_warning("SOURCE_BLOCK_OMITTED")
            continue
        if len(ranges) > 64:
            _add_warning("SOURCE_BLOCK_OMITTED")
        for rng in ranges[:64]:
            if not isinstance(rng, dict):
                _add_warning("SOURCE_BLOCK_OMITTED")
                continue
            text = rng.get("text")
            if not isinstance(text, str):
                _add_warning("SOURCE_BLOCK_OMITTED")
                continue
            start = rng.get("start", 1)
            blocks.append((epath, start if isinstance(start, int) and start > 0 else 1, text))

    literal_hints, seen_hints, omitted_literal_count, total_bytes = [], set(), 0, 0

    for path, start_line, text in blocks:
        if len(seen_hints) >= 512:
            _add_warning("SOURCE_BLOCK_OMITTED")
            break
        try:
            b = text.encode("utf-8")
        except Exception:
            _add_warning("SOURCE_BLOCK_OMITTED")
            continue
        if len(b) > 160000 or total_bytes + len(b) > 1000000:
            _add_warning("SOURCE_BLOCK_OMITTED")
            continue
        total_bytes += len(b)
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            _add_warning("SOURCE_BLOCK_OMITTED")
            continue

        try:
            for node, is_frag in _iter_ast_literals(tree):
                val = node.value
                if not any(unicodedata.category(c) == "Cf" for c in val):
                    continue
                key = (path, val)
                if key in seen_hints:
                    continue
                if len(seen_hints) >= 512:
                    _add_warning("SOURCE_BLOCK_OMITTED")
                    break
                seen_hints.add(key)

                json_lit = json.dumps(val, ensure_ascii=True)
                if len(json_lit) > 2048 or len(literal_hints) >= 16:
                    omitted_literal_count += 1
                    continue

                hint = {
                    "path": path,
                    "line": start_line + getattr(node, "lineno", 1) - 1,
                    "json_literal": json_lit,
                    "is_fragment": is_frag,
                }
                cand = {
                    "schema_version": 1,
                    "exact_blocker_ids": exact_blocker_ids,
                    "warnings": warnings,
                    "literal_hints": literal_hints + [hint],
                    "omitted_literal_count": omitted_literal_count,
                }
                if len(json.dumps(cand).encode("utf-8")) <= 8192:
                    literal_hints.append(hint)
                else:
                    omitted_literal_count += 1
        except (RecursionError, MemoryError):
            _add_warning("SOURCE_BLOCK_OMITTED")
            continue

    res = {
        "schema_version": 1,
        "exact_blocker_ids": exact_blocker_ids,
        "warnings": warnings,
        "literal_hints": literal_hints,
        "omitted_literal_count": omitted_literal_count,
    }
    while res["literal_hints"] and len(json.dumps(res).encode("utf-8")) > 8192:
        res["literal_hints"].pop()
        res["omitted_literal_count"] += 1
    while res["exact_blocker_ids"] and len(json.dumps(res).encode("utf-8")) > 8192:
        res["exact_blocker_ids"].pop()
        _add_warning("BLOCKER_IDS_OMITTED")
    while res["warnings"] and len(json.dumps(res).encode("utf-8")) > 8192:
        res["warnings"].pop()
    return res
