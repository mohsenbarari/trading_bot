"""Regression gates for a Qoder/Gemini-only operational Supervisor."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTOPILOT = ROOT / "scripts/two_site_codex_autopilot.py"


def _function(source: str, name: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    return next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _called_names(node: ast.AST) -> set[str]:
    return {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }


def test_operational_entrypoints_cannot_call_legacy_codex_runtime() -> None:
    source = AUTOPILOT.read_text()
    forbidden = {"codex_identity", "codex_command", "codex_runtime_identity"}
    for name in ("main", "run_loop", "run_cycle", "coordinator_identity", "coordinator_command"):
        assert _called_names(_function(source, name)).isdisjoint(forbidden), name


def test_cli_exposes_coordinator_without_codex_alias() -> None:
    source = AUTOPILOT.read_text()
    main = _function(source, "main")
    options = []
    for call in ast.walk(main):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "add_argument"):
            continue
        options.extend(
            arg.value for arg in call.args
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            and arg.value.startswith("--")
        )
    assert "--coordinator" in options
    assert "--codex" not in options


def test_operational_provider_is_fail_closed_to_qoder() -> None:
    source = AUTOPILOT.read_text()
    for name in ("coordinator_identity", "coordinator_command"):
        function = _function(source, name)
        comparisons = [node for node in ast.walk(function) if isinstance(node, ast.Compare)]
        assert any(
            isinstance(node.left, ast.Subscript)
            and isinstance(node.left.value, ast.Name) and node.left.value.id == "settings"
            and isinstance(node.left.slice, ast.Constant) and node.left.slice.value == "provider"
            and len(node.ops) == 1 and isinstance(node.ops[0], ast.NotEq)
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Constant)
            and node.comparators[0].value == "qoder"
            for node in comparisons
        )
        rendered = ast.unparse(function)
        assert "qoder_" in rendered
