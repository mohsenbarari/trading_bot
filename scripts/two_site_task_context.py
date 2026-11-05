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


PERSISTENCE_RE = re.compile(
    r"\b(?:database|sql|sqlite|orm|persisted[-_ ]rows?|columns?|tables?|durable[-_ ]records?)\b",
    re.IGNORECASE,
)

_RECONCILIATION_SCHEMA_PROFILE_ID = "P1_RECONCILIATION_SCHEMA_V1"
_RECONCILIATION_SCHEMA_PROFILE_RULES = (
    "derive exact recursive key sets and built-in container/key/leaf types from explicit schema: bool is not int, float is not int, str/dict subclasses are excluded where exact types required",
    "independently read canonical manifest and verify exact accounting/stage/pending batch, never count every scenario as a new seed or close unproven origin cells",
    "pin all supplied path/commit/SHA/selector identities from independent immutable reference literals/actual retained bytes, never reuse the implementation/input pin as expected oracle",
    "independently corrupt each required nested shape, missing/extra key, wrong type and commit/SHA/selector, with one valid baseline and check-specific failure expectations (unrelated ValueError must not make negative pass)",
    "Reviewer maps every requirement/positive/negative to actual assertions and reuse candidates before VERIFIED; PASS/covered_requirements/VERIFIED alone is not final acceptance",
)

_RECONCILIATION_TASK_ID_RE = re.compile(
    r"^(?:WA[-_])?P1[-_]00B(?=[^A-Za-z0-9]|$)",
    re.IGNORECASE,
)
_INVENTORY_CANONICAL_PATH_RE = re.compile(
    r"^docs/refactor/two-server/section-1/inventory/(?:seed-handler-coverage\.json|recent-characterization(?:\-[a-zA-Z0-9]+)*\.json)$"
)
_RECONCILIATION_TEST_PATH_RE = re.compile(
    r"^tests/test_p1_00b_characterization_reconciliation(?:_[a-zA-Z0-9]+)*\.py$"
)
_STAGE_LEDGER_PATH = "docs/refactor/two-server/section-1/00-stage-ledger.md"


def _build_reconciliation_schema_profile() -> dict:
    return {
        "profile_id": _RECONCILIATION_SCHEMA_PROFILE_ID,
        "rules": list(_RECONCILIATION_SCHEMA_PROFILE_RULES),
    }


def _is_reconciliation_schema_applicable(task, sources) -> bool:
    if not isinstance(task, dict) or not isinstance(sources, dict):
        return False

    task_id = task.get("task_id")
    if not isinstance(task_id, str):
        return False
    if not _RECONCILIATION_TASK_ID_RE.match(task_id.strip()):
        return False

    goal = task.get("goal")
    if not isinstance(goal, str):
        return False
    reqs = task.get("requirements")
    if reqs is not None:
        if not isinstance(reqs, list) or not all(isinstance(r, str) for r in reqs):
            return False

    contract_text = _task_contract_text(task)
    if not re.search(r"\breconciliation\b", contract_text, re.IGNORECASE):
        return False
    if not re.search(r"\b(?:schema|provenance|accounting|validation)\b", contract_text, re.IGNORECASE):
        return False

    if bool(PERSISTENCE_RE.search(contract_text)) or bool(
        re.search(r"\b(?:session|live[-_ ]session|db[-_ ]session)\b", contract_text, re.IGNORECASE)
    ):
        return False

    allowed_files = task.get("allowed_files")
    if not isinstance(allowed_files, list) or not allowed_files:
        return False

    has_target = False
    for p in allowed_files:
        if not isinstance(p, str) or not p:
            return False
        is_p1_test = bool(_RECONCILIATION_TEST_PATH_RE.fullmatch(p))
        is_inventory_json = bool(_INVENTORY_CANONICAL_PATH_RE.fullmatch(p))
        is_ledger = (p == _STAGE_LEDGER_PATH)

        if not (is_p1_test or is_inventory_json or is_ledger):
            return False
        if is_p1_test or is_inventory_json:
            has_target = True

    if not has_target:
        return False

    has_canonical_source = False
    for path, item in sources.items():
        if not isinstance(path, str):
            continue
        if _INVENTORY_CANONICAL_PATH_RE.fullmatch(path):
            content = item.get("content") if isinstance(item, dict) else item
            if isinstance(content, str) and content.strip():
                has_canonical_source = True
                break

    if not has_canonical_source:
        return False

    return True


def _task_contract_text(task) -> str:
    if not isinstance(task, dict):
        return ""
    goal = task.get("goal")
    if isinstance(goal, str):
        goal = goal.split("CANONICAL_SOURCE_EXCERPTS_JSON:", 1)[0]
    else:
        goal = ""
    requirements = task.get("requirements")
    if not isinstance(requirements, list):
        requirements = []
    return "\n".join([goal] + [item for item in requirements if isinstance(item, str)])


def _call_leaf(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _model_contract_hints(task, sources):
    """Return compact SQLAlchemy fixture facts for models named by the task."""
    contract_text = _task_contract_text(task)
    if not contract_text or not isinstance(sources, dict):
        return [], 0

    hints = []
    omitted = 0
    for path, item in sorted(sources.items(), key=lambda pair: str(pair[0])):
        if not isinstance(path, str) or not path.startswith("models/") or not path.endswith(".py"):
            continue
        content = item.get("content") if isinstance(item, dict) else item
        if not isinstance(content, str):
            continue
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            continue
        for model in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            if not re.search(rf"\b{re.escape(model.name)}\b", contract_text):
                continue
            for statement in model.body:
                if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    continue
                target = statement.target if isinstance(statement, ast.AnnAssign) else (
                    statement.targets[0] if len(statement.targets) == 1 else None
                )
                value = statement.value
                if not isinstance(target, ast.Name) or not isinstance(value, ast.Call):
                    continue
                if _call_leaf(value.func) not in {"Column", "mapped_column"} or not value.args:
                    continue
                column_type = value.args[0]
                type_name = _call_leaf(column_type.func) if isinstance(column_type, ast.Call) else _call_leaf(column_type)
                keywords = {kw.arg: kw.value for kw in value.keywords if kw.arg is not None}
                nullable_false = isinstance(keywords.get("nullable"), ast.Constant) and keywords["nullable"].value is False
                primary_key = isinstance(keywords.get("primary_key"), ast.Constant) and keywords["primary_key"].value is True
                has_default = "default" in keywords or "server_default" in keywords
                required = nullable_false and not primary_key and not has_default
                is_enum = type_name == "Enum"
                is_datetime = type_name in {"Date", "DateTime"}
                if not (required or is_enum or is_datetime):
                    continue
                if len(hints) >= 32:
                    omitted += 1
                    continue
                try:
                    rendered_type = ast.unparse(column_type)
                except Exception:
                    rendered_type = type_name or "unknown"
                hint = {
                    "path": path,
                    "model": model.name,
                    "field": target.id,
                    "column_type": rendered_type[:160],
                    "required_without_declared_default": required,
                }
                if is_enum and isinstance(column_type, ast.Call):
                    hint["enum_database_representation"] = (
                        "value" if any(kw.arg == "values_callable" for kw in column_type.keywords)
                        else "member_name"
                    )
                hints.append(hint)
    return hints, omitted


def _extract_direct_return_dict_keys(func_node) -> list[str] | None:
    if getattr(func_node, "decorator_list", None):
        return None
    if not func_node.body or not isinstance(func_node.body[-1], ast.Return):
        return None
    ret = func_node.body[-1]
    if ret.value is None or not isinstance(ret.value, ast.Dict):
        return None
    returns = []
    stack = list(func_node.body)
    while stack:
        curr = stack.pop()
        if isinstance(curr, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(curr, ast.Return):
            returns.append(curr)
        elif isinstance(curr, (ast.Yield, ast.YieldFrom)):
            return None
        stack.extend(ast.iter_child_nodes(curr))
    if len(returns) != 1 or returns[0] is not ret:
        return None
    keys = []
    for k in ret.value.keys:
        if k is None or not isinstance(k, ast.Constant) or not isinstance(k.value, str):
            return None
        keys.append(k.value)
    return sorted(set(keys))


def _target_names(target) -> set[str]:
    names = set()
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            names.update(_target_names(elt))
    elif isinstance(target, ast.Starred):
        names.update(_target_names(target.value))
    return names


def _collect_top_level_bindings(nodes) -> list[str]:
    names = []
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                names.extend(_target_names(t))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            names.extend(_target_names(node.target))
        elif isinstance(node, ast.Delete):
            for t in node.targets:
                names.extend(_target_names(t))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.append(alias.asname or alias.name)
        elif isinstance(node, ast.If):
            names.extend(_collect_top_level_bindings(node.body))
            names.extend(_collect_top_level_bindings(node.orelse))
        elif isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            names.extend(_collect_top_level_bindings(node.body))
            for h in getattr(node, "handlers", []):
                if h.name:
                    names.append(h.name)
                names.extend(_collect_top_level_bindings(h.body))
            names.extend(_collect_top_level_bindings(getattr(node, "orelse", [])))
            names.extend(_collect_top_level_bindings(getattr(node, "finalbody", [])))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars:
                    names.extend(_target_names(item.optional_vars))
            names.extend(_collect_top_level_bindings(node.body))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            names.extend(_target_names(node.target))
            names.extend(_collect_top_level_bindings(node.body))
            names.extend(_collect_top_level_bindings(node.orelse))
    return names


def _source_contract_hints(task, sources) -> tuple[list[dict], int]:
    """Return compact direct dict return facts for top-level functions named in task."""
    contract_text = _task_contract_text(task)
    if not contract_text or not isinstance(sources, dict):
        return [], 0

    referenced_names = set(re.findall(r"(?<!\w)[A-Za-z_]\w*(?!\w)", contract_text))
    if not referenced_names:
        return [], 0

    by_name = {}
    for path, item in sorted(sources.items(), key=lambda pair: str(pair[0])):
        if not isinstance(path, str) or not path.endswith(".py"):
            continue
        content = item.get("content") if isinstance(item, dict) else item
        if not isinstance(content, str):
            continue
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            continue
        top_bindings = _collect_top_level_bindings(tree.body)
        binding_counts = {}
        for name in top_bindings:
            binding_counts[name] = binding_counts.get(name, 0) + 1
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in referenced_names:
                if binding_counts.get(node.name, 0) > 1:
                    keys = None
                else:
                    keys = _extract_direct_return_dict_keys(node)
                by_name.setdefault(node.name, []).append((path, keys))

    candidates = []
    for fn_name, occurrences in by_name.items():
        if len(occurrences) != 1:
            continue
        path, keys = occurrences[0]
        if keys is None:
            continue
        candidates.append({
            "path": path,
            "function": fn_name,
            "keys": keys,
        })

    candidates.sort(key=lambda item: (item["path"], item["function"]))

    hints = []
    omitted = 0
    for cand in candidates:
        if len(hints) >= 16:
            omitted += 1
            continue
        hints.append(cand)

    return hints, omitted


def _is_python_test_file(path: str) -> bool:
    if not isinstance(path, str):
        return False
    parts = path.replace("\\", "/").strip().split("/")
    if len(parts) < 2:
        return False
    under_test = any(p in ("tests", "test") for p in parts[:-1])
    fn = parts[-1]
    is_test_fn = fn.endswith(".py") and (fn.startswith("test_") or fn.endswith("_test.py"))
    return bool(under_test and is_test_fn)


def _has_full_content(path: str, sources) -> bool:
    if not isinstance(sources, dict) or path not in sources:
        return False
    val = sources[path]
    content = val.get("content") if isinstance(val, dict) else val
    return content is not None


def _extract_model_imports(text: str):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        try:
            lines = text.splitlines()
            indents = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
            min_i = min(indents) if indents else 0
            tree = ast.parse("\n".join(l[min_i:] if len(l) >= min_i else l for l in lines))
        except Exception:
            return []
    except Exception:
        return []
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            if node.module == "models":
                for alias in node.names:
                    for sym in (alias.name, alias.asname):
                        if sym and sym.isidentifier():
                            results.append((f"models/{alias.name}.py", sym))
            elif node.module.startswith("models."):
                parts = node.module.split(".")
                mod_name = parts[1]
                for alias in node.names:
                    for sym in (alias.name, alias.asname):
                        if sym and sym.isidentifier():
                            results.append((f"models/{mod_name}.py", sym))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("models."):
                    parts = alias.name.split(".")
                    mod_name = parts[1]
                    for sym in (parts[-1], alias.asname):
                        if sym and sym.isidentifier():
                            results.append((f"models/{mod_name}.py", sym))
    return results


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


def _proposal_existing_file_budgets(profile: dict, sources: object) -> dict:
    limits = profile.get("file_limits", {}) if isinstance(profile, dict) else {}
    total_limit = profile.get("total_limit", 0) if isinstance(profile, dict) else 0
    sorted_paths = sorted(limits.keys())
    entries = []
    for path in sorted_paths[:8]:
        file_limit = limits[path]
        effective_limit = min(file_limit, total_limit)
        item = sources.get(path) if isinstance(sources, dict) and path in sources else None
        state = "UNKNOWN"
        baseline = None
        growth = None
        if type(item) is dict and "content" in item and "sha256" in item:
            c = item["content"]
            s = item["sha256"]
            if c is None and s is None:
                state = "NEW_FILE"
                baseline = 0
                growth = effective_limit
            elif (
                type(c) is str
                and type(s) is str
                and len(s) == 64
                and all(ch in "0123456789abcdef" for ch in s)
                and len(c) <= 160000
            ):
                try:
                    cb = c.encode("utf-8")
                    if len(cb) <= 160000:
                        state = "EXISTING"
                        baseline = len(cb)
                        growth = effective_limit - baseline
                except UnicodeEncodeError:
                    pass
        entries.append({
            "path": path,
            "state": state,
            "baseline_utf8_bytes": baseline,
            "file_limit_utf8_bytes": file_limit,
            "effective_single_file_limit_utf8_bytes": effective_limit,
            "max_net_growth_utf8_bytes": growth,
        })
    return {
        "version": 1,
        "entries": entries,
        "omitted_path_count": max(0, len(sorted_paths) - 8),
    }


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

    goal = task.get("goal") if isinstance(task, dict) else None
    if isinstance(goal, str) and "CANONICAL_SOURCE_EXCERPTS_JSON:\n" in goal:
        goal = goal.split("CANONICAL_SOURCE_EXCERPTS_JSON:\n", 1)[0]
    elif isinstance(goal, str) and "CANONICAL_SOURCE_EXCERPTS_JSON:" in goal:
        goal = goal.split("CANONICAL_SOURCE_EXCERPTS_JSON:", 1)[0]
    reqs = task.get("requirements") if isinstance(task, dict) else None
    req_list = reqs if isinstance(reqs, list) else ([reqs] if reqs is not None else [])
    goal_req_texts = [t for t in [goal] + req_list if isinstance(t, str)]
    persistence_contract_requested = any(bool(PERSISTENCE_RE.search(t)) for t in goal_req_texts)

    matched_model_paths = set()
    if persistence_contract_requested:
        py_texts = []
        if isinstance(sources, dict):
            for p, item in sorted_sources:
                if isinstance(p, str) and p.endswith(".py"):
                    c = item.get("content") if isinstance(item, dict) else item
                    if isinstance(c, str):
                        py_texts.append(c)
        for exc in raw_excerpts:
            if isinstance(exc, dict):
                ep = exc.get("path")
                if isinstance(ep, str) and ep.endswith(".py"):
                    rngs = exc.get("ranges")
                    if isinstance(rngs, list):
                        for rng in rngs:
                            if isinstance(rng, dict) and isinstance(rng.get("text"), str):
                                py_texts.append(rng["text"])
        for txt in py_texts:
            for canon_path, sym in _extract_model_imports(txt):
                if any(bool(re.search(rf"\b{re.escape(sym)}\b", t)) for t in goal_req_texts):
                    matched_model_paths.add(canon_path)

    sorted_models = sorted(matched_model_paths)
    if len(sorted_models) > 16:
        required_full_model_paths = sorted_models[:16]
        omitted_model_path_count = len(sorted_models) - 16
    else:
        required_full_model_paths = sorted_models
        omitted_model_path_count = 0

    missing_full_model_paths = [
        p for p in required_full_model_paths if not _has_full_content(p, sources)
    ]

    model_contract_hints, omitted_model_contract_hint_count = _model_contract_hints(task, sources)
    dep_ctx = {
        "persistence_contract_requested": persistence_contract_requested,
        "required_full_model_paths": required_full_model_paths,
        "missing_full_model_paths": missing_full_model_paths,
        "omitted_model_path_count": omitted_model_path_count,
        "model_contract_hints": model_contract_hints,
        "omitted_model_contract_hint_count": omitted_model_contract_hint_count,
    }

    allowed_files = task.get("allowed_files") if isinstance(task, dict) else None
    test_only_task = (
        isinstance(allowed_files, list)
        and len(allowed_files) > 0
        and all(_is_python_test_file(p) for p in allowed_files)
    )

    new_test_candidates = set()
    if isinstance(sources, dict):
        new_test_candidates.update(sources.keys())
    if isinstance(allowed_files, list):
        new_test_candidates.update(allowed_files)

    raw_new_tests = []
    if isinstance(sources, dict):
        for p in new_test_candidates:
            if isinstance(p, str) and _is_python_test_file(p):
                val = sources.get(p)
                if isinstance(val, dict) and val.get("sha256") is None and val.get("content") is None:
                    raw_new_tests.append(p)
    new_test_files = sorted(set(raw_new_tests))[:16]

    out_contract = {
        "max_provider_output_tokens": 60000,
        "max_test_only_transport_bytes": 64000,
        "max_new_test_file_utf8_bytes": 24000,
        "test_only_task": test_only_task,
        "new_test_files": new_test_files,
    }
    # Canonical task limits outrank the generic drafting hint, not vice versa.
    # Keep the complete detached profile even if advisory lists are later trimmed.
    try:
        from scripts.two_site_proposal_byte_profile import parse_proposal_utf8_budget_profile
    except ImportError:
        from two_site_proposal_byte_profile import parse_proposal_utf8_budget_profile
    profile = parse_proposal_utf8_budget_profile(reqs, allowed_files, allowed_files)
    if profile is not None:
        out_contract["proposal_utf8_budget_profile"] = profile
        if test_only_task:
            # Wire JSON includes escapes, metadata and prose beyond file bytes.
            # Keep the legacy floor and the existing whole-context hard ceiling.
            out_contract["max_test_only_transport_bytes"] = min(
                160000, max(64000, 2 * profile["total_limit"]
                            + 4096 * (len(profile["file_limits"]) + 1)),
            )
        out_contract["proposal_existing_file_budgets"] = _proposal_existing_file_budgets(profile, sources)
        # The machine byte profile is still a numbered physical requirement.
        # Expose every success coverage ID; do not infer coverage from R-labels.
        out_contract["success_covered_requirements"] = list(range(1, len(reqs) + 1))
        allowed_new_tests = sorted(set(raw_new_tests) & set(profile["file_limits"]))
        if allowed_new_tests:
            # Global drafting ceiling; the complete per-path profile below still
            # limits smaller siblings independently.
            cap = max(min(64000, profile["total_limit"], profile["file_limits"][path])
                      for path in allowed_new_tests)
            out_contract["max_new_test_file_utf8_bytes"] = cap
            if len(allowed_new_tests) == 1:
                out_contract["new_test_file_draft_target_utf8_bytes"] = (3 * cap) // 4
        # Multiple new files have independent caps in the profile. Never apply
        # the smallest path's cap globally to a larger permitted sibling.
        if any(path.startswith(("tests/test_p1_00_characterization_",
                                "tests/test_p1_00b_characterization_"))
               for path in allowed_new_tests):
            # Advisory only: do not rewrite proposals or weaken static/test/review gates.
            out_contract["new_characterization_scaffold"] = {"bootstrap_prefix":"from __future__ import annotations\nimport os\nos.environ[\"APP_ENV_FILE\"] = \"config/unit-test.env.example\"\nfrom tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence\ninstall_p1_00_network_fence()\n","fixture_import_rule":"Reuse unittest.TestCase fixtures through module imports, not imported class bindings (private aliases also collect). Verify whole-file pytest collection matches the task.","persistence_snapshot_rule":"When full-row invariance is required, independently compare every mapped persisted column, including time, nullable and JSON fields; a partial shared snapshot helper is not full-row proof."}

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
                    "dependency_context": dep_ctx,
                    "output_contract": out_contract,
                }
                if len(json.dumps(cand).encode("utf-8")) <= 8192:
                    literal_hints.append(hint)
                else:
                    omitted_literal_count += 1
        except (RecursionError, MemoryError):
            _add_warning("SOURCE_BLOCK_OMITTED")
            continue

    source_contract_hints, omitted_source_contract_hint_count = _source_contract_hints(task, sources)

    res = {
        "schema_version": 1,
        "exact_blocker_ids": exact_blocker_ids,
        "warnings": warnings,
        "literal_hints": literal_hints,
        "omitted_literal_count": omitted_literal_count,
        "source_contract_hints": source_contract_hints,
        "omitted_source_contract_hint_count": omitted_source_contract_hint_count,
        "dependency_context": dep_ctx,
        "output_contract": out_contract,
    }
    while res["source_contract_hints"] and len(json.dumps(res).encode("utf-8")) > 8192:
        res["source_contract_hints"].pop()
        res["omitted_source_contract_hint_count"] += 1
    while res["literal_hints"] and len(json.dumps(res).encode("utf-8")) > 8192:
        res["literal_hints"].pop()
        res["omitted_literal_count"] += 1
    while res["output_contract"]["new_test_files"] and len(json.dumps(res).encode("utf-8")) > 8192:
        res["output_contract"]["new_test_files"].pop()
    while res["dependency_context"]["model_contract_hints"] and len(json.dumps(res).encode("utf-8")) > 8192:
        res["dependency_context"]["model_contract_hints"].pop()
        res["dependency_context"]["omitted_model_contract_hint_count"] += 1
    while res["dependency_context"]["required_full_model_paths"] and len(json.dumps(res).encode("utf-8")) > 8192:
        popped = res["dependency_context"]["required_full_model_paths"].pop()
        res["dependency_context"]["omitted_model_path_count"] += 1
        if popped in res["dependency_context"]["missing_full_model_paths"]:
            res["dependency_context"]["missing_full_model_paths"].remove(popped)
    while res["dependency_context"]["missing_full_model_paths"] and len(json.dumps(res).encode("utf-8")) > 8192:
        res["dependency_context"]["missing_full_model_paths"].pop()
    while res["exact_blocker_ids"] and len(json.dumps(res).encode("utf-8")) > 8192:
        res["exact_blocker_ids"].pop()
        _add_warning("BLOCKER_IDS_OMITTED")
    while res["warnings"] and len(json.dumps(res).encode("utf-8")) > 8192:
        res["warnings"].pop()
    if _is_reconciliation_schema_applicable(task, sources):
        profile = _build_reconciliation_schema_profile()
        candidate = dict(res)
        candidate["reconciliation_schema_profile"] = profile
        if len(json.dumps(candidate).encode("utf-8")) <= 8192:
            res["reconciliation_schema_profile"] = profile
    if "proposal_utf8_budget_profile" in out_contract and len(json.dumps(res).encode("utf-8")) > 8192:
        raise ValueError("preparation notes exceed byte budget")
    return res
