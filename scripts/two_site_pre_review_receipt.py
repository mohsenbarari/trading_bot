"""Candidate-test receipt module for pre-review sandbox validation."""
from __future__ import annotations

import ast
from pathlib import Path
import re
from typing import Any, Mapping

try:
    from scripts.two_site_proposal_byte_profile import (
        ProposalByteProfileError, parse_proposal_utf8_budget_profile,
    )
except ImportError:
    from two_site_proposal_byte_profile import (
        ProposalByteProfileError, parse_proposal_utf8_budget_profile,
    )

try:
    from scripts import two_site_agent_sandbox
    from scripts.two_site_agent_sandbox import (
        SandboxError,
        _validate_overlay_path,
        selectors as sandbox_selectors,
    )
except ImportError:
    import two_site_agent_sandbox
    from two_site_agent_sandbox import (
        SandboxError,
        _validate_overlay_path,
        selectors as sandbox_selectors,
    )

try:
    from scripts.two_site_pre_review_tests import (
        normalize_test_selectors,
        tracked_test_inventory,
    )
except ImportError:
    from two_site_pre_review_tests import (
        normalize_test_selectors,
        tracked_test_inventory,
    )

__all__ = [
    "candidate_test_assertion_findings",
    "p1_00_characterization_bootstrap_findings",
    "run_candidate_tests",
    "validate_test_receipt",
]

MAX_SPECS, MAX_CHANGES, MAX_SELECTORS, MAX_TAIL_BYTES = 32, 32, 200, 8192
MAX_NEW_TEST_BYTES = 24000
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_RECEIPT_KEYS = frozenset({
    "state",
    "proposal_sha256",
    "selectors",
    "unsupported_specs",
    "returncode",
    "stdout_tail",
    "stderr_tail",
    "snapshot_files",
    "snapshot_bytes",
    "isolation",
})
P1_00_CHARACTERIZATION_TEST = re.compile(
    r"^tests/test_p1_00b?_characterization_[a-z0-9_]+\.py$"
)
TWO_SITE_TEST = re.compile(
    r"^tests/test_two_site_[A-Za-z0-9_]+\.py$"
)
NETWORK_FENCE_MODULE = "tests.test_p1_00_characterization_network_isolation"
NETWORK_FENCE_NAME = "install_p1_00_network_fence"
APPLICATION_IMPORT_ROOTS = frozenset({
    "api", "bot", "core", "main", "models", "run_bot", "schemas",
})


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return None if prefix is None else f"{prefix}.{node.attr}"
    return None


def _string_literal(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _application_import(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name.split(".", 1)[0] in APPLICATION_IMPORT_ROOTS for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module:
        return node.module.split(".", 1)[0] in APPLICATION_IMPORT_ROOTS
    return False


def _app_env_statement(node: ast.AST) -> tuple[bool, bool]:
    """Return whether a statement sets APP_ENV_FILE and uses the canonical value."""
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        call = node.value
        if (
            _dotted_name(call.func) == "os.environ.setdefault"
            and len(call.args) >= 2
            and _string_literal(call.args[0]) == "APP_ENV_FILE"
        ):
            return True, _string_literal(call.args[1]) == "config/unit-test.env.example"

    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, ast.Subscript) or _dotted_name(target.value) != "os.environ":
                continue
            if _string_literal(target.slice) == "APP_ENV_FILE":
                return True, _string_literal(node.value) == "config/unit-test.env.example"
    return False, False


def _canonical_fence_import(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.ImportFrom)
        and node.module == NETWORK_FENCE_MODULE
        and any(alias.name == NETWORK_FENCE_NAME for alias in node.names)
    )


def _canonical_fence_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and _dotted_name(node.value.func) == NETWORK_FENCE_NAME
        and not node.value.args
        and not node.value.keywords
    )


def _manual_socket_hook_assignment(node: ast.AST) -> bool:
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    elif isinstance(node, ast.AugAssign):
        targets = [node.target]
    return any(
        _dotted_name(target) in {
            "socket.socket.connect",
            "socket.socket.connect_ex",
            "socket.create_connection",
        }
        for target in targets
    )


def p1_00_characterization_bootstrap_findings(worker_result: Mapping[str, Any]) -> list[str]:
    """Find deterministic bootstrap defects before sandbox tests or Gemini review.

    This applies only to newly created P1-00 characterization test files that import
    application code. It keeps a sandbox's inherited environment from masking a
    non-hermetic committed test, without expanding into unrelated historical tests
    or replacing semantic review and real tests.
    """
    changes = worker_result.get("changes") if isinstance(worker_result, Mapping) else None
    if not isinstance(changes, list):
        return []

    findings: list[str] = []
    for change in changes:
        if not isinstance(change, Mapping):
            continue
        path, content = change.get("path"), change.get("content")
        if (
            not isinstance(path, str)
            or change.get("original_sha256") is not None
            or not P1_00_CHARACTERIZATION_TEST.fullmatch(path)
        ):
            continue
        if path == "tests/test_p1_00_characterization_network_isolation.py":
            continue
        if not isinstance(content, str):
            continue
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError:
            continue  # The existing Python syntax gate owns this diagnostic.

        application_lines = [
            node.lineno for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom)) and _application_import(node)
        ]
        if not application_lines:
            continue
        first_application_import = min(application_lines)

        environment_statements = [
            (node.lineno, canonical)
            for node in tree.body
            for present, canonical in [_app_env_statement(node)]
            if present
        ]
        if any(not canonical for _, canonical in environment_statements):
            findings.append(
                f"{path}: P1-00 characterization bootstrap uses a noncanonical APP_ENV_FILE value; "
                "use config/unit-test.env.example before application imports."
            )
        elif not any(canonical and line < first_application_import for line, canonical in environment_statements):
            findings.append(
                f"{path}: P1-00 characterization bootstrap must set APP_ENV_FILE to "
                "config/unit-test.env.example before application imports."
            )

        fence_import_lines = [node.lineno for node in tree.body if _canonical_fence_import(node)]
        fence_call_lines = [node.lineno for node in tree.body if _canonical_fence_call(node)]
        if not (
            fence_import_lines
            and fence_call_lines
            and min(fence_import_lines) < min(fence_call_lines) < first_application_import
        ):
            findings.append(
                f"{path}: P1-00 characterization bootstrap must import and call "
                "install_p1_00_network_fence() before application imports."
            )

        if any(_manual_socket_hook_assignment(node) for node in ast.walk(tree)):
            findings.append(
                f"{path}: P1-00 characterization tests must not assign socket connection hooks; "
                "reuse install_p1_00_network_fence()."
            )
    return findings[:32]


def _writes_name(node: ast.AST, name: str) -> bool:
    return any(
        (isinstance(c, ast.NamedExpr) and isinstance(c.target, ast.Name) and c.target.id == name)
        or (isinstance(c, ast.Name) and isinstance(getattr(c, "ctx", None), (ast.Store, ast.Del)) and c.id == name)
        for c in ast.walk(node)
    )


def _iter_scope_nodes(nodes: list[ast.AST]):
    """Yield AST nodes in the current scope without descending into nested function/class/comprehension scopes."""
    stack = list(reversed(nodes))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Lambda,
                ast.ListComp,
                ast.SetComp,
                ast.DictComp,
                ast.GeneratorExp,
            ),
        ):
            continue
        for child in reversed(list(ast.iter_child_nodes(node))):
            stack.append(child)


def _collect_scope_bound_names(nodes: list[ast.AST]) -> set[str]:
    """Collect direct variable, definition, and import names bound in the current scope."""
    bound: set[str] = set()
    for node in _iter_scope_nodes(nodes):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound.add(alias.asname if alias.asname else alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound.add(alias.asname if alias.asname else alias.name)
        elif isinstance(node, ast.Name) and isinstance(getattr(node, "ctx", None), (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
        elif isinstance(node, ast.ExceptHandler) and isinstance(node.name, str):
            bound.add(node.name)
        elif (
            isinstance(node, (getattr(ast, "MatchAs", ()), getattr(ast, "MatchStar", ())))
            and getattr(node, "name", None)
        ):
            bound.add(node.name)
    return bound


def _function_param_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return all parameter names (posonly, regular, kwonly, vararg, kwarg) for a function."""
    params = {
        arg.arg
        for arg in (
            func.args.posonlyargs
            + func.args.args
            + func.args.kwonlyargs
        )
    }
    if func.args.vararg:
        params.add(func.args.vararg.arg)
    if func.args.kwarg:
        params.add(func.args.kwarg.arg)
    return params


def _get_and_conjuncts(expr: ast.AST) -> list[ast.AST]:
    """Flatten boolean AND expressions into constituent conjuncts."""
    if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.And):
        res: list[ast.AST] = []
        for val in expr.values:
            res.extend(_get_and_conjuncts(val))
        return res
    return [expr]


def _find_functions_with_enclosing(
    node: ast.AST,
    enclosing_func_bound: set[str],
) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, set[str]]]:
    """Find all functions with their enclosing function bound names."""
    results: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, set[str]]] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            results.append((child, set(enclosing_func_bound)))
            local_bound = _function_param_names(child) | _collect_scope_bound_names(child.body)
            results.extend(_find_functions_with_enclosing(child, enclosing_func_bound | local_bound))
        elif isinstance(child, ast.ClassDef):
            results.extend(_find_functions_with_enclosing(child, enclosing_func_bound))
        else:
            results.extend(_find_functions_with_enclosing(child, enclosing_func_bound))
    return results


def _shallow_custody_findings(tree: ast.AST, path: str) -> list[tuple[int, str]]:
    """Identify shallow-custody snapshot assignments in candidate test functions.

    Note: This is a conservative subset detector, not proof of full deep custody;
    independent semantic review is mandatory.
    """
    module_bound_names = _collect_scope_bound_names(tree.body) if isinstance(tree, ast.Module) else set()
    findings: list[tuple[int, str]] = []

    for func, enclosing_func_bound in _find_functions_with_enclosing(tree, set()):
        func_params = _function_param_names(func)
        func_bound = func_params | _collect_scope_bound_names(func.body)
        shadowed = module_bound_names | enclosing_func_bound | func_bound

        equality_pairs: set[tuple[str, str]] = set()
        candidate_assignments: list[tuple[ast.AST, str, str, str]] = []

        for node in _iter_scope_nodes(func.body):
            if isinstance(node, ast.Assert):
                for conjunct in _get_and_conjuncts(node.test):
                    if isinstance(conjunct, ast.Compare):
                        cur_left = conjunct.left
                        for op, right in zip(conjunct.ops, conjunct.comparators):
                            if (
                                isinstance(op, ast.Eq)
                                and isinstance(cur_left, ast.Name)
                                and isinstance(right, ast.Name)
                            ):
                                equality_pairs.add((cur_left.id, right.id))
                                equality_pairs.add((right.id, cur_left.id))
                            cur_left = right
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "assertEqual"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"
                and len(node.args) == 2
                and not node.keywords
                and isinstance(node.args[0], ast.Name)
                and isinstance(node.args[1], ast.Name)
            ):
                equality_pairs.add((node.args[0].id, node.args[1].id))
                equality_pairs.add((node.args[1].id, node.args[0].id))

            target_name: str | None = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target_name = node.targets[0].id
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                target_name = node.target.id

            if target_name and (target_name.endswith("_snap") or target_name.endswith("_snapshot")):
                rhs = node.value
                if (
                    isinstance(rhs, ast.Call)
                    and isinstance(rhs.func, ast.Name)
                    and rhs.func.id in ("dict", "list")
                    and len(rhs.args) == 1
                    and isinstance(rhs.args[0], ast.Name)
                    and not rhs.keywords
                ):
                    param_name = rhs.args[0].id
                    if param_name in func_params and param_name != target_name:
                        candidate_assignments.append((node, param_name, target_name, rhs.func.id))

        for assign_node, param_name, snap_name, ctor in candidate_assignments:
            if ctor not in shadowed and (param_name, snap_name) in equality_pairs:
                findings.append((assign_node.lineno, f"{path}:{assign_node.lineno}: shallow-custody-snapshot"))

    return findings


def _p1_sql_column_identity_findings(tree: ast.AST, path: str) -> list[str]:
    """Detect only statement.left is Model.__table__.c.column or assertIs.

    SQLAlchemy may annotate/wrap a selected column. Python object identity does
    not establish its SQL column semantics. This is a narrow subset detector;
    metadata custody identity, None identity and exact param mappings are not
    defects. Alias/dataflow analysis and full semantic review remain separate.
    """
    def table_column(node: ast.AST) -> bool:
        name = _dotted_name(node)
        if name is not None:
            return bool(re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\.__table__\.c\.[A-Za-z_]\w*", name))
        return (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
            and bool(re.fullmatch(
                r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\.__table__\.c",
                _dotted_name(node.value) or "",
            ))
        )

    def statement_left(node: ast.AST) -> bool:
        return isinstance(node, ast.Attribute) and node.attr == "left"

    def bad_pair(first: ast.AST, second: ast.AST) -> bool:
        return (
            statement_left(first) and table_column(second)
            or table_column(first) and statement_left(second)
        )

    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            for expr in ast.walk(node.test):
                if not isinstance(expr, ast.Compare):
                    continue
                first = expr.left
                for op, second in zip(expr.ops, expr.comparators):
                    if isinstance(op, ast.Is) and bad_pair(first, second):
                        lines.add(node.lineno)
                    first = second
        elif isinstance(node, ast.Call) and _dotted_name(node.func) == "self.assertIs":
            if len(node.args) >= 2:
                first, second = node.args[:2]
            else:
                kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
                first, second = kwargs.get("first"), kwargs.get("second")
            if first is not None and second is not None and bad_pair(first, second):
                lines.add(node.lineno)
    return [
        f"{path}:{line}: sqlalchemy-column-object-identity; use exact compiled SQL "
        "and column-specific parameter mapping, not Python identity of wrapped columns."
        for line in sorted(lines)
    ]


def candidate_test_assertion_findings(worker_result: Mapping[str, Any]) -> list[str]:
    """Find deterministic assertion and shallow-custody defects in candidate two-site tests.

    Subset detector, not proof of full deep custody; semantic review mandatory.
    """
    changes = worker_result.get("changes") if isinstance(worker_result, Mapping) else None
    if not isinstance(changes, list):
        return []

    findings: list[str] = []
    for change in changes:
        if not isinstance(change, Mapping):
            continue
        path = change.get("path")
        content = change.get("content")
        if (
            isinstance(path, str)
            and P1_00_CHARACTERIZATION_TEST.fullmatch(path)
            and "original_sha256" in change
            and change["original_sha256"] is None
        ):
            if isinstance(content, str):
                try:
                    tree = ast.parse(content, filename=path)
                except SyntaxError:
                    continue  # The existing syntax gate owns this diagnostic.
                findings.extend(_p1_sql_column_identity_findings(tree, path))
            continue
        if (
            not isinstance(path, str)
            or not TWO_SITE_TEST.fullmatch(path)
        ):
            continue
        if not isinstance(content, str):
            continue
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError:
            continue  # The existing Python syntax gate owns this diagnostic.

        file_findings: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                for expr in ast.walk(node.test):
                    if isinstance(expr, ast.IfExp):
                        if isinstance(expr.test, ast.Constant) and (expr.test.value is True or expr.test.value is False):
                            file_findings.append((node.lineno, f"{path}:{node.lineno}: constant-branch"))
                            break
                    elif isinstance(expr, ast.Compare):
                        left = expr.left
                        matched = False
                        for op, right in zip(expr.ops, expr.comparators):
                            if (
                                isinstance(op, (ast.In, ast.NotIn))
                                and isinstance(left, ast.Constant)
                                and isinstance(right, ast.Set)
                                and right.elts
                                and all(isinstance(elt, ast.Constant) for elt in right.elts)
                                and any(type(elt.value) is type(left.value) and elt.value == left.value for elt in right.elts)
                            ):
                                file_findings.append((node.lineno, f"{path}:{node.lineno}: constant-set"))
                                matched = True
                                break
                            left = right
                        if matched:
                            break
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for s1, s2 in zip(node.body, node.body[1:]):
                    if (
                        isinstance(s1, ast.Assign)
                        and len(s1.targets) == 1
                        and isinstance(s1.targets[0], ast.Name)
                        and isinstance(s1.value, ast.Constant)
                        and isinstance(s2, ast.Assert)
                    ):
                        tname, lit = s1.targets[0].id, s1.value.value
                        matched = False
                        for cmp in ast.walk(s2.test):
                            if isinstance(cmp, ast.Compare):
                                cur_left = cmp.left
                                for op, right in zip(cmp.ops, cmp.comparators):
                                    if (
                                        isinstance(op, ast.In)
                                        and isinstance(cur_left, ast.Constant)
                                        and type(cur_left.value) is type(lit)
                                        and cur_left.value == lit
                                        and isinstance(right, ast.Name)
                                        and right.id == tname
                                    ):
                                        matched = True
                                        break
                                    cur_left = right
                                if matched:
                                    break
                        if matched and not _writes_name(s2.test, tname):
                            file_findings.append((s2.lineno, f"{path}:{s2.lineno}: self-proving"))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "test_selectors":
                    dflt = node.args[1] if len(node.args) >= 2 else next(
                        (kw.value for kw in node.keywords if kw.arg == "default"), None
                    )
                    if dflt is not None and not (isinstance(dflt, ast.Constant) and dflt.value is None):
                        file_findings.append((node.lineno, f"{path}:{node.lineno}: selector-default"))

        for item in _shallow_custody_findings(tree, path):
            file_findings.append(item)

        file_findings.sort(key=lambda item: (item[0], item[1]))
        findings.extend(item[1] for item in file_findings)
    return findings[:32]


def _bound_utf8_tail(text: str, max_bytes: int = MAX_TAIL_BYTES) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    tail = encoded[-max_bytes:]
    for offset in range(min(4, len(tail))):
        try:
            return tail[offset:].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return tail.decode("utf-8", errors="ignore")


def validate_test_receipt(
    receipt: Mapping[str, Any],
    proposal_sha256: str,
    require_passed: bool = False,
) -> dict[str, Any]:
    """Validate persisted test receipt schema and return normalized copy."""
    if not isinstance(receipt, Mapping) or isinstance(receipt, (str, bytes)):
        raise SandboxError("receipt must be a mapping")
    if not isinstance(require_passed, bool):
        raise SandboxError("require_passed must be a bool")
    if set(receipt.keys()) != EXPECTED_RECEIPT_KEYS:
        raise SandboxError("receipt keys must match expected schema exactly")

    if not isinstance(proposal_sha256, str) or not SHA_PATTERN.fullmatch(proposal_sha256):
        raise SandboxError("proposal_sha256 must be a lowercase 64-hex SHA")
    receipt_sha = receipt["proposal_sha256"]
    if not isinstance(receipt_sha, str) or not SHA_PATTERN.fullmatch(receipt_sha):
        raise SandboxError("receipt proposal_sha256 must be a lowercase 64-hex SHA")
    if receipt_sha != proposal_sha256:
        raise SandboxError("receipt proposal_sha256 does not match expected SHA")

    rc = receipt["returncode"]
    if not isinstance(rc, int) or isinstance(rc, bool):
        raise SandboxError("receipt returncode must be a non-bool integer")
    state = receipt["state"]
    if not isinstance(state, str):
        raise SandboxError("receipt state must be a string")
    if state == "PASSED":
        if rc != 0:
            raise SandboxError("receipt state PASSED requires returncode 0")
    elif state == "FAILED":
        if rc == 0:
            raise SandboxError("receipt state FAILED requires nonzero returncode")
    else:
        raise SandboxError("receipt state must be PASSED or FAILED")

    if require_passed and state != "PASSED":
        raise SandboxError("receipt state must be PASSED when require_passed=True")

    raw_selectors = receipt["selectors"]
    if not isinstance(raw_selectors, list):
        raise SandboxError("receipt selectors must be a list")
    sandbox_selectors(raw_selectors)

    raw_unsupported = receipt["unsupported_specs"]
    if not isinstance(raw_unsupported, list):
        raise SandboxError("receipt unsupported_specs must be a list")
    if len(raw_unsupported) > MAX_SPECS:
        raise SandboxError("receipt unsupported_specs cannot exceed 32 entries")
    seen_specs: set[str] = set()
    for spec in raw_unsupported:
        if not isinstance(spec, str) or not spec:
            raise SandboxError("receipt unsupported_specs entries must be nonempty strings")
        try:
            spec.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SandboxError("receipt unsupported_specs entries must be valid UTF-8") from exc
        if spec in seen_specs:
            raise SandboxError("receipt unsupported_specs entries must be unique")
        seen_specs.add(spec)

    for key in ("stdout_tail", "stderr_tail"):
        val = receipt[key]
        if not isinstance(val, str):
            raise SandboxError(f"receipt {key} must be a string")
        try:
            encoded = val.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SandboxError(f"receipt {key} must be valid UTF-8") from exc
        if len(encoded) > MAX_TAIL_BYTES:
            raise SandboxError(f"receipt {key} exceeds {MAX_TAIL_BYTES} bytes")

    for key in ("snapshot_files", "snapshot_bytes"):
        val = receipt[key]
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            raise SandboxError(f"receipt {key} must be a non-negative integer")

    isolation = receipt["isolation"]
    if not isinstance(isolation, str) or not isolation:
        raise SandboxError("receipt isolation must be a non-empty string")
    try:
        isolation.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SandboxError("receipt isolation must be valid UTF-8") from exc

    return {
        "state": state,
        "proposal_sha256": receipt_sha,
        "selectors": list(raw_selectors),
        "unsupported_specs": list(raw_unsupported),
        "returncode": rc,
        "stdout_tail": receipt["stdout_tail"],
        "stderr_tail": receipt["stderr_tail"],
        "snapshot_files": receipt["snapshot_files"],
        "snapshot_bytes": receipt["snapshot_bytes"],
        "isolation": isolation,
    }


def _is_canonical_new_test(change: Mapping[str, Any]) -> bool:
    if "original_sha256" not in change or change["original_sha256"] is not None:
        return False
    path = change.get("path")
    if not isinstance(path, str):
        return False
    parts = Path(path).parts
    if len(parts) < 2 or parts[0] != "tests":
        return False
    name = parts[-1]
    return name.startswith("test_") and name.endswith(".py")


def run_candidate_tests(
    root: str | Path,
    task: Mapping[str, Any],
    worker_result: Mapping[str, Any],
    proposal_sha256: str,
    timeout: int = 1200,
) -> dict[str, Any]:
    """Execute candidate tests through sandbox and return bounded receipt."""
    if isinstance(root, str):
        if not root.strip():
            raise SandboxError("root must be a nonempty path")
        root = Path(root)
    elif not isinstance(root, Path):
        raise SandboxError(f"root must be a str or Path, got {type(root).__name__}")
    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SandboxError("root must be an existing directory") from exc
    if not resolved_root.is_dir():
        raise SandboxError("root must be an existing directory")

    if not isinstance(proposal_sha256, str) or not SHA_PATTERN.fullmatch(proposal_sha256):
        raise SandboxError("proposal_sha256 must be a lowercase 64-hex SHA")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not (1 <= timeout <= 3600):
        raise SandboxError("timeout must be an integer between 1 and 3600")

    if not isinstance(task, Mapping) or isinstance(task, (str, bytes)):
        raise SandboxError("task must be a mapping")
    raw_specs = task.get("test_selectors")
    if (not isinstance(raw_specs, (list, tuple)) or isinstance(raw_specs, (str, bytes))
            or not (1 <= len(raw_specs) <= MAX_SPECS)
            or any(not isinstance(s, str) for s in raw_specs)):
        raise SandboxError("task test_selectors must be 1..32 strings")

    if not isinstance(worker_result, Mapping) or isinstance(worker_result, (str, bytes)):
        raise SandboxError("worker_result must be a mapping")
    raw_changes = worker_result.get("changes")
    if (not isinstance(raw_changes, (list, tuple)) or isinstance(raw_changes, (str, bytes))
            or not (1 <= len(raw_changes) <= MAX_CHANGES)):
        raise SandboxError("Worker changes must be 1..32 entries")

    overlays: dict[str, str] = {}
    for change in raw_changes:
        if not isinstance(change, Mapping) or isinstance(change, (str, bytes)):
            raise SandboxError("Worker change must be a mapping")
        path, content = change.get("path"), change.get("content")
        if not isinstance(path, str) or not path.strip():
            raise SandboxError("Worker change path must be a nonempty string")
        _validate_overlay_path(path)
        if path in overlays:
            raise SandboxError(f"duplicate overlay path: {path}")
        if not isinstance(content, str) or not content or "\x00" in content:
            raise SandboxError("Worker change content must be nonempty NUL-free string")
        try:
            content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SandboxError("Worker change content must be valid UTF-8") from exc
        overlays[path] = content

    inventory = tracked_test_inventory(resolved_root, list(overlays.keys()))
    chosen_selectors: list[str] = []
    chosen_set: set[str] = set()
    unsupported_specs: list[str] = []
    seen_unsupported: set[str] = set()

    for spec in raw_specs:
        try:
            spec_selectors, _ = normalize_test_selectors([spec], inventory)
        except SandboxError:
            if spec not in seen_unsupported:
                seen_unsupported.add(spec)
                unsupported_specs.append(spec)
            continue
        if not spec_selectors:
            if spec not in seen_unsupported:
                seen_unsupported.add(spec)
                unsupported_specs.append(spec)
            continue

        new_selectors: list[str] = []
        for s in spec_selectors:
            if s not in chosen_set and s not in new_selectors:
                new_selectors.append(s)

        if len(chosen_selectors) + len(new_selectors) <= MAX_SELECTORS:
            for s in new_selectors:
                chosen_selectors.append(s)
                chosen_set.add(s)
        else:
            if spec not in seen_unsupported:
                seen_unsupported.add(spec)
                unsupported_specs.append(spec)

    if not chosen_selectors:
        raise SandboxError("at least one focused selector required")

    profile_findings: list[str] = []
    try:
        profile = parse_proposal_utf8_budget_profile(
            task.get("requirements"), task.get("allowed_files"), list(overlays),
        )
    except ProposalByteProfileError as exc:
        profile_findings.append(str(exc))
        profile = None
    if profile is not None:
        new_tests = {c["path"] for c in raw_changes if _is_canonical_new_test(c)}
        total_bytes = 0
        for path in sorted(overlays):
            byte_count = len(overlays[path].encode("utf-8"))
            total_bytes += byte_count
            cap = profile["file_limits"][path]
            if path in new_tests:
                cap = min(cap, MAX_NEW_TEST_BYTES)
            if byte_count > cap:
                profile_findings.append(f"AssertionError: {path} exceeds {cap} byte limit: {byte_count} bytes")
        if total_bytes > profile["total_limit"]:
            profile_findings.append(
                f"AssertionError: proposal total exceeds {profile['total_limit']} byte limit: {total_bytes} bytes"
            )
    if profile_findings:
        return validate_test_receipt({
            "state": "FAILED", "proposal_sha256": proposal_sha256,
            "selectors": chosen_selectors, "unsupported_specs": unsupported_specs,
            "returncode": 1, "stdout_tail": "",
            "stderr_tail": _bound_utf8_tail("\n".join(profile_findings)),
            "snapshot_files": 0, "snapshot_bytes": 0, "isolation": "static-byte-profile",
        }, proposal_sha256)

    oversize_findings: list[str] = []
    for change in raw_changes:
        if _is_canonical_new_test(change):
            path = change["path"]
            content = change["content"]
            byte_count = len(content.encode("utf-8"))
            if byte_count > MAX_NEW_TEST_BYTES:
                oversize_findings.append(
                    f"AssertionError: {path} exceeds {MAX_NEW_TEST_BYTES} byte limit: {byte_count} bytes"
                )
    if oversize_findings:
        bounded_findings = oversize_findings[:MAX_CHANGES]
        receipt = {
            "state": "FAILED",
            "proposal_sha256": proposal_sha256,
            "selectors": chosen_selectors,
            "unsupported_specs": unsupported_specs,
            "returncode": 1,
            "stdout_tail": "",
            "stderr_tail": _bound_utf8_tail("\n".join(bounded_findings)),
            "snapshot_files": 0,
            "snapshot_bytes": 0,
            "isolation": "static-byte-budget",
        }
        return validate_test_receipt(receipt, proposal_sha256)

    sandbox_result = two_site_agent_sandbox.run_tests(
        resolved_root,
        chosen_selectors,
        timeout=timeout,
        overlays=overlays,
    )

    if not isinstance(sandbox_result, Mapping) or isinstance(sandbox_result, (str, bytes)):
        raise SandboxError("malformed sandbox result: expected mapping")
    rc = sandbox_result.get("returncode")
    if not isinstance(rc, int) or isinstance(rc, bool):
        raise SandboxError("malformed sandbox result: returncode must be int")
    stdout, stderr = sandbox_result.get("stdout"), sandbox_result.get("stderr")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise SandboxError("malformed sandbox result: stdout/stderr must be str")
    snapshot_files = sandbox_result.get("snapshot_files")
    snapshot_bytes = sandbox_result.get("snapshot_bytes")
    for name, val in (("snapshot_files", snapshot_files), ("snapshot_bytes", snapshot_bytes)):
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            raise SandboxError(f"malformed sandbox result: {name} must be non-negative int")
    isolation = sandbox_result.get("isolation")
    if not isinstance(isolation, str) or not isolation:
        raise SandboxError("malformed sandbox result: isolation must be non-empty str")

    receipt = {
        "state": "PASSED" if rc == 0 else "FAILED",
        "proposal_sha256": proposal_sha256,
        "selectors": chosen_selectors,
        "unsupported_specs": unsupported_specs,
        "returncode": rc,
        "stdout_tail": _bound_utf8_tail(stdout),
        "stderr_tail": _bound_utf8_tail(stderr),
        "snapshot_files": snapshot_files,
        "snapshot_bytes": snapshot_bytes,
        "isolation": isolation,
    }
    return validate_test_receipt(receipt, proposal_sha256)
