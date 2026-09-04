#!/usr/bin/env python3
"""Read-only, fail-closed custom-agent binding checks for AGY CLI 1.1.26.

The CLI's init.tools is a global catalog, not the selected agent's capabilities.
This small version-specific adapter checks the runtime-owned SQLite metadata. It
never reads token stores and never opens SQLite with immutable=1 (which skips WAL).
Unknown layouts fail closed and require a reviewed adapter update.
"""

from __future__ import annotations

import re
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import yaml

UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
MODEL = "gemini-3.8-flash-high"


def fields(data: bytes) -> dict[int, list[Any]]:
    """Decode bounded protobuf wire fields without depending on private classes."""
    if not isinstance(data, bytes) or len(data) > 4_000_000:
        raise ValueError("invalid or oversized Antigravity metadata")
    pos = 0
    result: dict[int, list[Any]] = {}

    def varint() -> int:
        nonlocal pos
        value = 0
        for shift in range(0, 70, 7):
            if pos >= len(data):
                raise ValueError("truncated metadata varint")
            byte = data[pos]
            pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError("oversized metadata varint")

    while pos < len(data):
        tag = varint()
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError("invalid metadata field")
        if wire == 0:
            value: Any = varint()
        elif wire in (1, 2, 5):
            size = varint() if wire == 2 else (8 if wire == 1 else 4)
            if pos + size > len(data):
                raise ValueError("truncated metadata value")
            value = data[pos:pos + size]
            pos += size
        else:
            raise ValueError("unsupported metadata wire type")
        result.setdefault(number, []).append(value)
    return result


def one(data: bytes, *path: int, default: Any = None) -> Any:
    value: Any = data
    for number in path:
        values = fields(value).get(number, [])
        if not values:
            return default
        if len(values) != 1:
            raise ValueError("ambiguous metadata field")
        value = values[0]
    return value


def binding(
    session_id: str,
    name: str,
    root: Path,
    expected_tools: set[str],
    *,
    parent_id: str | None = None,
    cli_home: Path | None = None,
) -> dict[str, Any]:
    """Require persisted custom role, source URI, sandbox and exact tool allowlist."""
    if not isinstance(session_id, str) or not UUID.fullmatch(session_id):
        raise ValueError("invalid smoke conversation ID")
    home = cli_home or Path.home() / ".gemini" / "antigravity-cli"
    database = home / "conversations" / f"{session_id}.db"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        identities = db.execute("SELECT cascade_id,trajectory_type,source FROM trajectory_meta").fetchall()
        rows = db.execute("SELECT data FROM trajectory_metadata_blob WHERE id='main'").fetchall()
        steps = db.execute("SELECT status,error_details FROM steps").fetchall()
        executors = db.execute("SELECT data FROM executor_metadata").fetchall()
    if identities != [(session_id, 4, 17)] or len(rows) != 1 or not isinstance(rows[0][0], bytes):
        raise ValueError("conversation database identity mismatch")
    data = rows[0][0]
    if len(steps) < 2 or any(status != 3 or error not in (None, b"") for status, error in steps):
        raise ValueError("smoke session has unfinished or failed execution steps")
    if not executors or any(one(row[0], 10, 1, 28) != MODEL.encode() for row in executors):
        raise ValueError("actual execution model is not the reviewed High-effort model")
    source = (root / ".agents" / "agents" / name / "agent.md").resolve().as_uri().encode()
    is_main = name == "two-site-coordinator"
    agent_file = root / ".agents" / "agents" / name / "agent.md"
    definition = agent_file.read_text(encoding="utf-8")
    parts = definition.split("---", 2)
    if len(parts) != 3 or parts[0] or not parts[2].startswith("\n\n# System Prompt\n\n") or not parts[2].endswith("\n"):
        raise ValueError("unexpected agent definition envelope")
    meta = yaml.safe_load(parts[1])
    if not isinstance(meta, dict) or any(meta.get(key) != value for key, value in {
        "name": name, "mainAgent": is_main, "subagent": not is_main,
        "model": "inherit", "commandExecutionPolicy": "sandbox",
    }.items()) or set(meta.get("tools", [])) != expected_tools:
        raise ValueError("source agent metadata mismatch")
    checks = {
        "selected_resolved_config": one(data, 4, 7) == one(data, 10),
        "name": one(data, 4, 1) == name.encode(),
        "description": one(data, 4, 8) == meta["description"].encode(),
        "main": one(data, 4, 13) == int(is_main),
        "subagent": one(data, 4, 12) == int(not is_main),
        "model_tier": one(data, 4, 15) == 1,
        "source": one(data, 4, 7, 17, 16) == source,
        "resolved_source": one(data, 10, 17, 16) == source,
        "sandbox": one(data, 4, 7, 5) == b"sandbox",
        "resolved_sandbox": one(data, 10, 5) == b"sandbox",
    }
    actual_tools: set[str] = set()
    tool_parent = one(data, 10, 18, 1)
    if not isinstance(tool_parent, bytes):
        raise ValueError("missing resolved tool allowlist")
    for item in fields(tool_parent).get(3, []):
        if set(fields(item)) != {1, 2}:
            raise ValueError("unexpected resolved tool fields")
        tool_name = one(item, 1)
        if not isinstance(tool_name, bytes) or one(item, 2, default=0) != 1:
            raise ValueError("unexpected resolved tool entry")
        decoded = tool_name.decode("utf-8")
        if decoded in actual_tools:
            raise ValueError("duplicate resolved tool")
        actual_tools.add(decoded)
    checks["tools"] = actual_tools == expected_tools
    prompts = fields(tool_parent).get(2, [])
    text = one(prompts[0], 1, 2) if prompts else None
    body = parts[2][len("\n\n# System Prompt\n\n"):-1]
    checks["system_prompt_label"] = bool(prompts) and one(prompts[0], 1, 1) == b"System Prompt"
    checks["system_prompt"] = text == body.encode("utf-8")
    if parent_id is not None:
        checks["parent"] = one(data, 5) == parent_id.encode()
        checks["root"] = one(data, 6) == parent_id.encode()
        checks["child_type"] = one(data, 8, 1) == name.encode()
        checks["child_role"] = one(data, 8, 2) == (b"Stage Worker" if name.endswith("worker") else b"Stage Verifier")
        checks["child_flag"] = one(data, 17) == 1
    elif is_main:
        checks["root"] = one(data, 6) == session_id.encode()
        checks["parent"] = one(data, 5) is None
        checks["no_child_spec"] = one(data, 8) is None and one(data, 17) is None
    failed = [key for key, passed in checks.items() if not passed]
    if failed:
        raise ValueError(f"custom-agent binding mismatch for {name}: {', '.join(failed)}")
    return {"conversation_id": session_id, "name": name, "tools": sorted(actual_tools),
            "source": source.decode(), "parent_id": parent_id,
            "definition_sha256": hashlib.sha256(definition.encode()).hexdigest()}
