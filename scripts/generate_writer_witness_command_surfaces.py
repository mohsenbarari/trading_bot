#!/usr/bin/python3.12
"""Mechanically derive controller and remote-host process call sites."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "scripts/plan_writer_witness_real_host_matrix.py",
    "scripts/run_writer_witness_real_host_matrix.py",
)
SCHEMA = "writer_witness_command_surfaces_v1"


class SurfaceError(RuntimeError):
    """The generated host/process boundary is incomplete or drifted."""


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _expression(node: ast.AST | None) -> str:
    return ast.unparse(node) if node is not None else ""


class CallCollector(ast.NodeVisitor):
    def __init__(self, relative: str) -> None:
        self.relative = relative
        self.functions: list[str] = []
        self.local: list[dict[str, object]] = []
        self.remote: list[dict[str, object]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        function = self.functions[-1] if self.functions else "<module>"
        if name in {
            "subprocess.run",
            "subprocess.Popen",
            "subprocess.check_call",
        }:
            argv = node.args[0] if node.args else None
            self.local.append(
                {
                    "argv_expression": _expression(argv),
                    "call": name,
                    "function": function,
                    "line": node.lineno,
                    "source": self.relative,
                }
            )
        elif name.endswith(".command") and node.args:
            # Controller.command is the only dynamic subprocess funnel.  Its
            # implementation performs an exact runtime allow-list check.
            self.local.append(
                {
                    "argv_expression": _expression(node.args[1] if len(node.args) > 1 else None),
                    "call": "Controller.command",
                    "function": function,
                    "line": node.lineno,
                    "source": self.relative,
                }
            )
        if name.endswith(".remote") and len(node.args) >= 3:
            self.remote.append(
                {
                    "command_expression_sha256": hashlib.sha256(
                        ast.dump(node.args[2], include_attributes=False).encode()
                    ).hexdigest(),
                    "function": function,
                    "host_role_expression": _expression(node.args[0]),
                    "label_expression": _expression(node.args[1]),
                    "line": node.lineno,
                    "source": self.relative,
                }
            )
        elif name == "CheckSpec" and len(node.args) >= 3:
            self.remote.append(
                {
                    "command_expression_sha256": hashlib.sha256(
                        ast.dump(node.args[1], include_attributes=False).encode()
                    ).hexdigest(),
                    "function": function,
                    "host_role_expression": _expression(node.args[2]),
                    "label_expression": _expression(node.args[0]),
                    "line": node.lineno,
                    "source": self.relative,
                }
            )
        self.generic_visit(node)


def build_surface() -> dict[str, object]:
    local: list[dict[str, object]] = []
    remote: list[dict[str, object]] = []
    source_sha256: dict[str, str] = {}
    for relative in SOURCES:
        path = ROOT / relative
        raw = path.read_bytes()
        source_sha256[relative] = hashlib.sha256(raw).hexdigest()
        tree = ast.parse(raw, filename=relative)
        collector = CallCollector(relative)
        collector.visit(tree)
        local.extend(collector.local)
        remote.extend(collector.remote)
    roles = {str(item["host_role_expression"]) for item in remote}
    for required in (
        "'control'",
        "'matrix_witness'",
        "'rollback_witness'",
        "'webapp_fi'",
        "'webapp_ir'",
    ):
        if required not in roles:
            raise SurfaceError(f"generated command surface lacks fixed role {required}")
    if not local or not remote:
        raise SurfaceError("generated command surface is unexpectedly empty")
    return {
        "controller_process_calls": local,
        "remote_host_calls": remote,
        "schema_version": SCHEMA,
        "source_sha256": source_sha256,
    }


def canonical_bytes(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()


def build_review_artifact() -> dict[str, object]:
    detailed = build_surface()
    local = detailed["controller_process_calls"]
    remote = detailed["remote_host_calls"]
    assert isinstance(local, list) and isinstance(remote, list)
    return {
        "controller_process_call_count": len(local),
        "controller_process_surface_sha256": hashlib.sha256(
            canonical_bytes({"calls": local})
        ).hexdigest(),
        "remote_host_expression_counts": dict(
            sorted(
                Counter(str(item["host_role_expression"]) for item in remote).items()
            )
        ),
        "remote_process_call_count": len(remote),
        "remote_process_surface_sha256": hashlib.sha256(
            canonical_bytes({"calls": remote})
        ).hexdigest(),
        "schema_version": SCHEMA,
        "source_sha256": detailed["source_sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--emit", action="store_true")
    mode.add_argument("--emit-details", action="store_true")
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    observed = canonical_bytes(build_review_artifact())
    if args.emit:
        sys.stdout.buffer.write(observed)
        return
    if args.emit_details:
        sys.stdout.buffer.write(canonical_bytes(build_surface()))
        return
    expected = args.verify.read_bytes()
    if expected != observed:
        raise SurfaceError("checked-in generated command surface is stale")
    print("writer_witness_command_surfaces_attested=yes")


if __name__ == "__main__":
    try:
        main()
    except (OSError, SyntaxError, SurfaceError, ValueError) as exc:
        raise SystemExit(f"Writer Witness command-surface generation failed: {exc}") from exc
