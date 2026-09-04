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
import json
import copy
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import yaml

UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
MODEL = "gemini-3.8-flash-high"
PROFILE = Path(__file__).with_name("antigravity_cli_1_1_26_profile.json")
PROFILE_KEYS = {
    "schema_version",
    "cli_version",
    "description",
    "child_resolved_aux",
    "executor_base",
    "source_config_variants",
    "executor_wrapper",
    "outer_metadata_11_hex",
    "executor_variants",
}
PROMPT_REFERENCES = ("user_information", "mcp_servers", "skills", "messaging",
                     "artifacts", "guidelines", "communication_style")
CONTINUATION_CHECKS = ("message_continue_check", "terminal_step_check",
                       "max_generator_invocations_check", "empty_output_continuation_check",
                       "force_invocation", "no_tool_call_check")
RESOLVED_AUX = bytes.fromhex("0a086a0642041a023004")
RESOLVED_FLAGS = bytes.fromhex("0a02100112020801220208013a020801")


def load_profile(path: Path | None = None) -> dict[str, Any]:
    """Load the exact reviewed CLI profile; unknown or malformed metadata blocks."""
    path = PROFILE if path is None else path
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate Antigravity profile key: {key}")
            result[key] = value
        return result

    profile = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if not isinstance(profile, dict) or set(profile) != PROFILE_KEYS:
        raise ValueError("Antigravity profile keys do not match the reviewed schema")
    if profile["schema_version"] != "1.0" or profile["cli_version"] != "1.1.26":
        raise ValueError("Antigravity profile schema/CLI version is not reviewed")
    if not isinstance(profile["description"], str) or not profile["description"].strip():
        raise ValueError("Antigravity profile description is invalid")
    for key in ("child_resolved_aux", "executor_base"):
        if not isinstance(profile[key], dict) or not profile[key]:
            raise ValueError(f"Antigravity profile {key} is invalid")
    for key in ("source_config_variants", "executor_wrapper"):
        if (not isinstance(profile[key], list) or not profile[key]
                or any(not isinstance(item, dict) or not item for item in profile[key])):
            raise ValueError(f"Antigravity profile {key} is invalid")
    if (not isinstance(profile["executor_variants"], list)
            or not profile["executor_variants"]
            or any(not isinstance(item, dict) for item in profile["executor_variants"])):
        raise ValueError("Antigravity profile executor_variants is invalid")
    opaque = profile["outer_metadata_11_hex"]
    if (not isinstance(opaque, list) or not opaque
            or any(not isinstance(item, str) or not item for item in opaque)):
        raise ValueError("Antigravity profile outer metadata variants are invalid")
    if len(set(opaque)) != len(opaque):
        raise ValueError("Antigravity profile outer metadata variants are invalid")
    try:
        if any(bytes.fromhex(item).hex() != item for item in opaque):
            raise ValueError("non-canonical hex")
    except ValueError as exc:
        raise ValueError("Antigravity profile outer metadata hex is invalid") from exc
    return profile


def wire_fields(data: bytes) -> list[tuple[int, int, Any]]:
    """Decode bounded protobuf wire fields without depending on private classes."""
    if not isinstance(data, bytes) or len(data) > 4_000_000:
        raise ValueError("invalid or oversized Antigravity metadata")
    pos = 0
    result = []

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
        result.append((number, wire, value))
    return result


def fields(data: bytes) -> dict[int, list[Any]]:
    result: dict[int, list[Any]] = {}
    for number, _wire, value in wire_fields(data):
        result.setdefault(number, []).append(value)
    return result


def message(*pairs: tuple[int, Any]) -> bytes:
    """Encode the reviewed length-delimited/varint configuration envelope."""
    def varint(value: int) -> bytes:
        result = bytearray()
        while value > 127:
            result.append((value & 127) | 128)
            value >>= 7
        return bytes(result) + bytes([value])
    result = b""
    for number, value in pairs:
        if isinstance(value, str):
            value = value.encode()
        if isinstance(value, int):
            result += varint(number << 3) + varint(value)
        else:
            result += varint(number << 3 | 2) + varint(len(value)) + value
    return result


def wire_tree(data: bytes, root: Path, depth: int = 0) -> dict[str, Any]:
    """Canonical wire-preserving tree; only documented path roots are variable.

    Unknown fields/values remain in the comparison, not silently discarded. Field
    order is immaterial, repeated-entry order is retained (except CLI feature-map
    order normalized separately). Fixed32/fixed64 cannot masquerade as protobuf
    strings. This is not a decoder for arbitrary user conversation contents.
    """
    if depth > 40:
        raise ValueError("metadata nesting limit exceeded")
    result: dict[str, Any] = {}
    for number, wire, value in wire_fields(data):
        if isinstance(value, int) and value > 9_007_199_254_740_991:
            value = {"uint": str(value)}
        elif isinstance(value, bytes):
            try:
                literal = value.decode("ascii")
            except UnicodeError:
                literal = None
            if wire == 2 and literal and all(32 <= ord(c) < 127 or c == "\n" for c in literal):
                value = {"text": literal.replace(str(root.resolve()), "${REPO}").replace(
                    str(Path.home() / ".gemini"), "${GEMINI}")}
            elif wire == 2:
                try:
                    value = {"message": wire_tree(value, root, depth + 1)}
                except ValueError:
                    value = {"hex": value.hex()}
            else:
                value = {"hex": value.hex()}
        result.setdefault(str(number), []).append({"wire": wire, "value": value})
    return result


def profile_patch(base: Any, patch: Any) -> Any:
    """Apply the small checked-in fixture deltas, never runtime-provided patches."""
    if isinstance(patch, dict) and set(patch) == {"$single"}:
        return [profile_patch(base[0], patch["$single"])]
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif key in result:
            result[key] = profile_patch(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def singleton_shape(data: bytes, required: dict[int, int], optional: dict[int, int] | None = None) -> None:
    allowed = {**required, **(optional or {})}
    items = wire_fields(data)
    if (len(items) != len({number for number, _wire, _value in items})
            or not set(required) <= {number for number, _wire, _value in items}
            or any(number not in allowed or allowed[number] != wire for number, wire, _value in items)):
        raise ValueError("unknown/duplicate/wrong-wire executor configuration shape")


def executor_envelope(data: bytes, root: Path, tool_container: bytes, profile: dict,
                      resolved_container: bytes) -> dict:
    singleton_shape(data, {1: 0, 2: 0, 3: 0, 9: 2, 10: 2, 11: 2, 17: 2})
    if one(data, 1) != 4 or any(not 0 <= one(data, k) < 1_000_000_000 for k in (2, 3)):
        raise ValueError("unexpected executor type/step boundaries")
    try:
        valid_uuid = UUID.fullmatch(one(data, 9).decode("ascii"))
    except UnicodeError:
        valid_uuid = None
    if not valid_uuid:
        raise ValueError("invalid executor generation UUID")
    # Do not infer that opaque metadata is harmless telemetry. Only the exact
    # observed, reviewed variants are admitted; changes need a profile review.
    if one(data, 11).hex() not in profile["outer_metadata_11_hex"]:
        raise ValueError("unreviewed opaque executor metadata")
    wrapper = one(data, 10)
    singleton_shape(wrapper, {k: 2 for k in (1, 2, 3, 4, 7, 9, 11, 13)}, {12: 2})
    source = one(data, 17)
    singleton_shape(source, {1: 2, 3: 2}, {12: 2})
    for candidate in (wrapper, source):
        if one(candidate, 12) not in (None, root.resolve().as_uri().encode()):
            raise ValueError("executor workspace scope drift")
    siblings = wire_tree(wrapper, root)
    siblings.pop("1")
    siblings.pop("12", None)
    if siblings not in profile["executor_wrapper"]:
        raise ValueError("unreviewed executor wrapper prompts/configuration")
    if one(source, 3) != bytes.fromhex("880101"):
        raise ValueError("unreviewed executor source configuration flags")
    source_config = wire_tree(one(source, 1), root)
    if (source_config.pop("46", None) != wire_tree(message((46, resolved_container)), root)["46"]
            or fields(one(source, 1, 46)) != fields(resolved_container)):
        raise ValueError("executor source prompt/permission envelope drift")
    if source_config not in profile["source_config_variants"]:
        raise ValueError("unreviewed executor source model/permission/configuration envelope")
    config = one(data, 10, 1)
    if not isinstance(config, bytes):
        raise ValueError("missing actual executor configuration")
    actual = wire_tree(config, root)
    expected_prompt = wire_tree(message((46, tool_container)), root)
    if (actual.pop("46", None) != expected_prompt["46"]
            or fields(one(config, 46)) != fields(tool_container)):
        raise ValueError("actual executor prompt/tools/continuation envelope drift")
    # Protobuf serializes this feature map in nondeterministic iteration order.
    # Normalize only that map; duplicate entries and all values stay significant.
    try:
        flags = actual["43"][0]["value"]["message"]["1"]
        flags.sort(key=lambda item: item["value"]["message"]["1"][0]["value"]["text"])
    except (KeyError, IndexError, TypeError):
        raise ValueError("unknown executor feature-map layout") from None
    variants = [profile_patch(profile["executor_base"], delta)
                for delta in profile["executor_variants"]]
    if actual not in variants:
        raise ValueError("unreviewed actual executor model/effort/permission/configuration envelope")
    return actual


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
    history_policy: str = "CLEAN_SMOKE",
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
        steps = db.execute("SELECT status,error_details FROM steps ORDER BY idx").fetchall()
        executors = db.execute("SELECT data FROM executor_metadata").fetchall()
    if identities != [(session_id, 4, 17)] or len(rows) != 1 or not isinstance(rows[0][0], bytes):
        raise ValueError("conversation database identity mismatch")
    data = rows[0][0]
    if history_policy not in {"CLEAN_SMOKE", "ACTIVE_EXECUTION"}:
        raise ValueError("unknown history policy")
    if len(steps) < 2 or any(error not in (None, b"") for _status, error in steps):
        raise ValueError("smoke session has unfinished or failed execution steps")
    statuses = [status for status, _error in steps]
    if history_policy == "CLEAN_SMOKE" and any(status != 3 for status in statuses):
        raise ValueError("smoke session has unfinished execution steps")
    if history_policy == "ACTIVE_EXECUTION":
        # A fence check is itself an in-flight tool step. Never resume its caller.
        first_active = next((i for i, status in enumerate(statuses) if status != 3), len(statuses))
        if first_active == 0 or any(status not in {1, 2} for status in statuses[first_active:]):
            raise ValueError("execution session has unknown/interleaved unfinished steps")
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
    # Complete known CLI 1.1.26 role envelope: no second literal prompt, extra
    # reference, auxiliary config, continuation control, permission or model flag
    # can be appended unnoticed. The profile contains sanitized config only.
    profile = load_profile()
    expected_container = message(
        (2, message((1, message((1, "System Prompt"), (2, body))))),
        *[(2, message((2, ref))) for ref in PROMPT_REFERENCES],
        *[(3, message((1, tool), (2, 1))) for tool in meta["tools"]],
        *[(5, message((1, check), (2, 1))) for check in CONTINUATION_CHECKS],
    )
    checks["complete_prompt_tool_envelope"] = wire_tree(tool_parent, root) == wire_tree(expected_container, root)
    expected_config = wire_tree(message(
        (5, "sandbox"), (11, RESOLVED_AUX), (17, message((16, source))),
        (18, message((1, expected_container), (3, RESOLVED_AUX), (4, RESOLVED_FLAGS))),
    ), root)
    if not is_main:
        expected_config["11"][0]["value"] = {"message": profile["child_resolved_aux"]}
    checks["complete_resolved_config"] = wire_tree(one(data, 10), root) == expected_config
    checks["complete_selected_role"] = set(fields(one(data, 4))) == {1, 7, 8, 12, 13, 15}
    top = fields(data)
    checks["complete_trajectory_shape"] = set(top) == ({1, 2, 3, 4, 6, 7, 10, 18} if is_main else
                                                      {1, 2, 3, 4, 5, 6, 7, 8, 10, 17, 18}) and all(len(v) == 1 for v in top.values())
    checks["workspace"] = one(data, 7) == root.resolve().as_uri().encode()
    executor_container = expected_container
    if not is_main:
        executor_container += message((2, message((2, "subagent_reminder"))),
                                      (8, message((1, "idle_subagent_guard"), (2, 1))))
    for row in executors:
        executor_envelope(row[0], root, executor_container, profile, expected_container)
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
            "definition_sha256": hashlib.sha256(definition.encode()).hexdigest(),
            "envelope_sha256": hashlib.sha256(json.dumps(
                {"resolved": expected_config, "executor_profile": profile,
                 "executor_prompt": wire_tree(executor_container, root)},
                sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
