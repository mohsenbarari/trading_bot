"""Narrow Qoder headless adapter; acceptance and Git remain controller gates."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time

from scripts.two_site_ai_config import load_ai_config, load_runtime_config

MAX_STREAM_BYTES = 16_000_000
MAX_RESULT_BYTES = 64_000
SETTINGS = {"general": {"enableAutoUpdate": False}}
TOOLS = {"coordinator": ("Bash", "Read", "Edit", "Write", "Glob", "Grep"),
         "final_reviewer": (), "receipt_formatter": (),
         "proposal_author": ()}
COORDINATOR_SCHEMA_REL = Path("docs/refactor/two-server/templates/codex-autopilot-cycle.schema.json")


class QoderError(ValueError):
    """A Qoder identity, lifecycle or result check failed."""


class QoderTerminalBodyError(QoderError):
    """SDK success was validated, but terminal text is not a JSON object.

    This does not assert a normal process exit or authorize acceptance/recovery.
    The process owner must independently establish its actual exit and custody.
    """


def _need(ok: object, code: str) -> None:
    if not ok:
        raise QoderError(code)


def _pairs(items):
    value = {}
    for key, item in items:
        _need(key not in value, "QODER_DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def _json(raw: str | bytes):
    def reject(_value):
        raise QoderError("QODER_NONFINITE_JSON")
    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=reject)
    except (ValueError, UnicodeError) as error:
        raise QoderError("QODER_INVALID_JSON") from error


def _regular(path: Path, maximum: int, *, executable: bool = False) -> bytes:
    path = Path(path)
    descriptor = None
    try:
        _need(path.is_absolute() and path.resolve(strict=True) == path, "QODER_PATH_ALIAS")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        before = os.fstat(descriptor)
        _need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
              and before.st_nlink == 1 and not before.st_mode & 0o022
              and 0 < before.st_size <= maximum, "QODER_FILE_CUSTODY")
        _need(not executable or bool(before.st_mode & 0o111), "QODER_NOT_EXECUTABLE")
        parts = []
        remaining = maximum + 1
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                break
            parts.append(block); remaining -= len(block)
        raw = b"".join(parts)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        after = os.fstat(descriptor); final = path.lstat()
        _need(len(raw) == before.st_size and all(getattr(before, k) == getattr(after, k)
              == getattr(final, k) for k in fields), "QODER_FILE_CHANGED")
        return raw
    except QoderError:
        raise
    except OSError as error:
        raise QoderError("QODER_FILE_UNREADABLE") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _probe(command: list[str], temporary: Path) -> bytes:
    """Bound status/version in RAM; never retain account metadata or diagnostics."""
    process = subprocess.Popen(command, cwd=temporary, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    selector = selectors.DefaultSelector(); output = bytearray(); total = 0
    try:
        for stream in (process.stdout, process.stderr):
            selector.register(stream, selectors.EVENT_READ)
        deadline = time.monotonic() + 15
        while selector.get_map():
            _need(time.monotonic() < deadline, "QODER_PROBE_TIMEOUT")
            for key, _ in selector.select(min(0.1, max(0, deadline-time.monotonic()))):
                block = os.read(key.fileobj.fileno(), 4096)
                if not block:
                    selector.unregister(key.fileobj); continue
                total += len(block)
                _need(total <= 65536, "QODER_PROBE_OUTPUT_BOUND")
                if key.fileobj is process.stdout:
                    output.extend(block)
        _need(process.wait(timeout=max(0.1, deadline-time.monotonic())) == 0, "QODER_PROBE_FAILED")
        return bytes(output)
    finally:
        selector.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        process.stdout.close(); process.stderr.close()


def _runtime(runtime: dict) -> dict:
    _need(type(runtime) is dict, "QODER_RUNTIME_SHAPE")
    value = runtime.get("coordinator", runtime)
    _need(type(value) is dict and {"path", "version", "sha256"} <= set(value)
          and set(value) <= {"path", "version", "sha256", "config_dir"}, "QODER_RUNTIME_FIELDS")
    _need(all(type(value[k]) is str and value[k] for k in ("path", "version", "sha256"))
          and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]), "QODER_RUNTIME_IDENTITY")
    return value


def _settings_args(runtime: dict) -> list[str]:
    result = ["--settings", json.dumps(SETTINGS, separators=(",", ":"))]
    if runtime.get("config_dir"):
        directory = Path(runtime["config_dir"])
        _need(directory.is_absolute() and directory.resolve(strict=True) == directory
              and directory.is_dir(), "QODER_CONFIG_DIRECTORY")
        result += ["--config-dir", str(directory)]
    return result


def _execution_options(root: Path, roleconfig: dict, role: str) -> tuple[dict, list[str]]:
    """Return documented Qoder settings and one bounded thinking mode.

    The model selector remains role data.  The execution limit is optional so
    historical configurations keep their exact argv; when present it is bound
    dynamically to the selected role model rather than to a model name in code.
    A fixed thinking ceiling retains the configured reasoning effort while
    preventing a model from consuming the whole output allowance in private
    reasoning without emitting its required terminal JSON.
    """
    policy = load_ai_config(root)
    settings = {"general": {"enableAutoUpdate": False}}
    _need(role in TOOLS, "QODER_ROLE")
    options = ["--thinking", "adaptive"]
    execution = policy.get("qoder_execution")
    if execution is not None:
        settings["modelConfigs"] = {"customOverrides": [{
            "match": {"model": roleconfig["model"], "overrideScope": "core"},
            "modelConfig": {"generateContentConfig": {
                "maxOutputTokens": execution["max_output_tokens"]}},
        }]}
        budget = execution.get("role_max_thinking_tokens", {}).get(
            role, execution.get("max_thinking_tokens"))
        if budget is not None:
            options = ["--thinking-budget", str(budget)]
        if "max_model_request_retries" in execution:
            options += ["--max-model-request-retries",
                        str(execution["max_model_request_retries"])]
    return settings, options


def qoder_identity(path: Path, runtime: dict, temporary: Path, roleconfig: dict | None = None) -> dict:
    runtime = _runtime(runtime); path = Path(path)
    _need(str(path) == runtime["path"], "QODER_RUNTIME_PATH_MISMATCH")
    before = _regular(path, 512 * 1024 * 1024, executable=True)
    digest = hashlib.sha256(before).hexdigest(); del before
    _need(digest == runtime["sha256"], "QODER_BINARY_PIN")
    prefix = [str(path), *_settings_args(runtime)]
    version = _probe(prefix + ["--version"], temporary).decode("utf-8").strip()
    _need(version == runtime["version"], "QODER_VERSION_PIN")
    status = _json(_probe(prefix + ["status", "--output", "json"], temporary))
    _need(type(status) is dict and status.get("logged_in") is True, "QODER_NOT_LOGGED_IN")
    _need(hashlib.sha256(_regular(path, 512 * 1024 * 1024, executable=True)).hexdigest() == digest,
          "QODER_BINARY_CHANGED")
    identity = {"provider": "qoder", "path": str(path), "version": version,
                "sha256": digest, "logged_in": True, "auto_update_enabled": False}
    if roleconfig is not None:
        _role(roleconfig)
        identity.update(model=roleconfig["model"], reasoning=roleconfig["reasoning_effort"])
    return identity


def _role(roleconfig: dict) -> None:
    _need(type(roleconfig) is dict and roleconfig.get("provider") == "qoder"
          and type(roleconfig.get("model")) is str and roleconfig["model"]
          and type(roleconfig.get("reasoning_effort")) is str
          and roleconfig["reasoning_effort"] in {"low", "medium", "high", "xhigh", "max"}, "QODER_ROLE_CONFIG")


def _coordinator_output_contract(root: Path) -> str:
    schema = _json(_regular(root / COORDINATOR_SCHEMA_REL, 65536))
    _need(type(schema) is dict and schema.get("type") == "object"
          and schema.get("additionalProperties") is False
          and type(schema.get("properties")) is dict
          and type(schema.get("required")) is list
          and all(type(key) is str for key in schema["required"])
          and len(schema["required"]) == len(set(schema["required"]))
          and set(schema["required"]) == set(schema["properties"]), "QODER_COORDINATOR_SCHEMA")
    return ("Your terminal result MUST be ONLY one JSON object matching the complete closed schema below. "
        "Include every required key exactly once and no additional keys. Do not return human prose, "
        "Markdown, fences or a summary outside the JSON. Populate values only from actual observed "
        "work and verified evidence; never invent acceptance, commit or role IDs. A successful bounded "
        "unit with remaining plan work requires CONTINUE. COMPLETE means the entire canonical approved "
        "two-site plan is genuinely closed, never merely this unit or one Stage. Cross-field rules are "
        "mandatory even though the transport schema cannot express them: CONTINUE/COMPLETE require "
        "disposition=NONE, category=NONE and blockers=[]; BLOCKED requires nonempty blockers and either "
        "RESCOPE+INTERNAL_REJECTION or HALT+EXTERNAL_GATE; RESCOPE/HALT require null commit and null "
        "Worker/Reviewer/proposal evidence; RESCOPE and CONTINUE require a concrete next_action. Use the "
        "truthful BLOCKED result when work cannot be accepted. Tool calls during the cycle remain "
        "subject to the existing permissions and repository gates. REQUIRED_KEYS_JSON="
        + json.dumps(schema["required"], separators=(",", ":"))
        + "\nTERMINAL_RESULT_SCHEMA_JSON:\n"
        + json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _final_reviewer_output_contract(response_schema: dict | None = None) -> str:
    schema = response_schema if response_schema is not None else {
        "type": "object",
        "additionalProperties": False,
        "required": ["decision", "findings", "proposal_sha256", "source_head"],
        "properties": {
            "decision": {"type": "string", "enum": ["APPROVE", "REQUEST_CHANGES"]},
            "findings": {"type": "array", "maxItems": 32,
                         "items": {"type": "string", "minLength": 1, "maxLength": 4096}},
            "proposal_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "source_head": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        },
    }
    _need(type(schema) is dict and schema.get("type") == "object"
          and schema.get("additionalProperties") is False,
          "QODER_FINAL_REVIEW_SCHEMA")
    task_rules = ("APPROVE requires findings=[]; REQUEST_CHANGES requires concrete nonempty "
                  "findings. Copy the exact proposal SHA and source head supplied in the user prompt. "
                  if response_schema is None else
                  "Populate every field only from the supplied review request and schema. ")
    return ("You are a tool-free final semantic reviewer. Return ONLY one JSON object matching "
            "this complete closed schema. Do not use tools, execute commands, edit, delegate, "
            "return Markdown, or add prose outside the JSON. " + task_rules
            + "TERMINAL_RESULT_SCHEMA_JSON:\n"
            + json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def qoder_command(path: Path, root: Path, run_dir: Path, prompt: str, roleconfig: dict,
                  *, role: str = "coordinator", response_schema: dict | None = None) -> list[str]:
    """Caller supplies exact prompt bytes to stdin and owns bounded capture/exit."""
    _role(roleconfig); _need(role in TOOLS, "QODER_ROLE")
    limit = 320000 if role in {"final_reviewer", "proposal_author"} else 160000
    _need(type(prompt) is str and 0 < len(prompt.encode("utf-8")) <= limit, "QODER_PROMPT_BOUND")
    root = Path(root).resolve(strict=True); run_dir = Path(run_dir).resolve(strict=True)
    _need(run_dir.is_relative_to(root) and run_dir != root, "QODER_RUN_DIRECTORY")
    runtime = _runtime(load_runtime_config(root)); _need(str(path) == runtime["path"], "QODER_RUNTIME_PATH_MISMATCH")
    contract = (["--append-system-prompt", _coordinator_output_contract(root)]
                if role in {"coordinator", "receipt_formatter"}
                else ["--append-system-prompt", _final_reviewer_output_contract(response_schema)]
                if role == "final_reviewer" else [])
    if response_schema is not None and role not in {"final_reviewer", "proposal_author"}:
        _need(False, "QODER_RESPONSE_SCHEMA_ROLE")
    if role == "proposal_author":
        _need(type(response_schema) is dict and response_schema.get("type") == "object"
              and response_schema.get("additionalProperties") is False
              and type(response_schema.get("properties")) is dict
              and set(response_schema.get("required", [])) == set(response_schema["properties"]),
              "QODER_AUTHOR_SCHEMA")
        contract = ["--append-system-prompt", "You are a tool-free proposal author. Return ONLY one "
                    "Worker JSON object matching this closed schema; no coordinator receipt, tools, "
                    "commands, delegation or edits.\n" + json.dumps(response_schema, ensure_ascii=False,
                                                                  separators=(",", ":"))]
    elif role != "final_reviewer":
        _need(response_schema is None, "QODER_UNEXPECTED_RESPONSE_SCHEMA")
    if role == "proposal_author":
        _need(len(prompt.encode("utf-8")) + len(contract[1].encode("utf-8")) <= limit,
              "QODER_AUTHOR_COMPLETE_PROMPT_BOUND")
    settings, thinking = _execution_options(root, roleconfig, role)
    settings_args = ["--settings", json.dumps(settings, separators=(",", ":"))]
    if runtime.get("config_dir"):
        settings_args += _settings_args(runtime)[2:]
    return [str(path), *settings_args, *contract, "--cwd", str(root), "--model", roleconfig["model"],
        "--reasoning-effort", roleconfig["reasoning_effort"], *thinking,
        "--output-format", "stream-json", "--no-session-persistence", "--permission-mode", "auto",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--tools", *(TOOLS[role] or ("",)), "--print"]


def _tool_blocks(event: dict):
    message = event.get("message", {})
    if type(message) is dict and type(message.get("content")) is list:
        for block in message["content"]:
            if type(block) is dict and block.get("type") == "tool_use":
                yield block
    wrapped = event.get("event", {})
    if type(wrapped) is dict:
        block = wrapped.get("content_block", {})
        if type(block) is dict and block.get("type") == "tool_use":
            yield block


def _stream_events(run_dir: Path, stream_filename: str) -> list[dict]:
    raw = _regular(run_dir / stream_filename, MAX_STREAM_BYTES)
    _need(raw.endswith(b"\n"), "QODER_TRUNCATED_STREAM")
    events = [_json(line) for line in raw.splitlines() if line.strip()]
    _need(events and all(type(event) is dict for event in events), "QODER_EVENT_SHAPE")
    return events


def _session_id(init: dict) -> str:
    session = init.get("session_id")
    _need(type(session) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", session), "QODER_SESSION_ID")
    return session


def _init_gates(init: dict, session: str, expected_model: str | None, role: str) -> None:
    if expected_model is not None:
        _need(init.get("model") == expected_model, "QODER_MODEL_MISMATCH")
    advertised = init.get("tools")
    if advertised is not None:
        # Qoder advertises intrinsic discovery even with an empty tool list.
        # Advertisement is not permission to execute MCP or delegation tools.
        discovery = ("mcp_call", "mcp_get", "mcp_list")
        _need(type(advertised) is list and all(type(name) is str and name in (*TOOLS[role], *discovery) for name in advertised),
              "QODER_UNEXPECTED_TOOLS")
    _need(init.get("mcp_servers", []) == [] and init.get("plugins", []) == [],
          "QODER_UNEXPECTED_EXTENSIONS")


def _event_gates(event: dict, session: str, role: str) -> None:
    _need(event.get("type") in {"system", "assistant", "user", "stream_event", "result"},
          "QODER_EVENT_TYPE")
    _need(event.get("session_id", session) == session, "QODER_CROSS_SESSION")
    for nested in (event.get("message"), event.get("event")):
        if type(nested) is dict:
            _need(nested.get("session_id", session) == session, "QODER_CROSS_SESSION")
    if event.get("type") in {"assistant", "user"}:
        _need(type(event.get("message")) is dict
              and type(event["message"].get("content")) is list, "QODER_MESSAGE_SHAPE")
    _need(event.get("type") not in {"error", "tool_error"}
          and event.get("subtype") not in {"error", "error_during_execution", "error_max_turns"}, "QODER_ERROR_EVENT")
    for block in _tool_blocks(event):
        _need(block.get("name") in TOOLS[role], "QODER_FORBIDDEN_TOOL")


def _result_gates(result: dict, session: str) -> str:
    _need(result.get("subtype") == "success" and result.get("is_error") is False
          and result.get("session_id") == session and not result.get("permission_denials")
          and not result.get("error") and result.get("stop_reason") == "end_turn", "QODER_NOT_SUCCESS")
    if "num_turns" in result:
        _need(type(result["num_turns"]) is int and result["num_turns"] >= 1, "QODER_TURN_COUNT")
    _need(type(result.get("result")) is str, "QODER_MISSING_RESULT")
    return result["result"]


def _task_notification(event: dict) -> bool:
    if event.get("type") == "system" and event.get("subtype") == "task_notification":
        return True
    message = event.get("message")
    return (type(message) is dict and type(message.get("content")) is list
            and any(type(block) is dict and block.get("type") == "task_notification"
                    for block in message["content"]))


def validate_qoder_terminal(run_dir: Path, *, stream_filename: str = "stream.jsonl",
                            expected_model: str | None = None, role: str = "coordinator") -> dict:
    """Validate the whole genuine SDK stream without parsing or rewriting its text."""
    _need(role in TOOLS and stream_filename in {"stream.jsonl", "qoder.stdout", "codex.stdout"}, "QODER_STREAM_NAME")
    run_dir = Path(run_dir).resolve(strict=True)
    events = _stream_events(run_dir, stream_filename)
    initial = [event for event in events if event.get("type") == "system" and event.get("subtype") == "init"]
    terminal = [event for event in events if event.get("type") == "result"]
    _need(len(initial) == len(terminal) == 1 and events[0] is initial[0] and events[-1] is terminal[0],
          "QODER_TERMINAL_CARDINALITY")
    session = _session_id(initial[0])
    _init_gates(initial[0], session, expected_model, role)
    for event in events:
        _event_gates(event, session, role)
    return {"session_id": session, "text": _result_gates(terminal[0], session)}


def validate_qoder_owned_wait_terminal(run_dir: Path, *, stream_filename: str = "stream.jsonl",
                                       expected_model: str | None = None,
                                       role: str = "coordinator") -> dict:
    """Validate a genuine two-lifecycle owned-wait stream; never parse its text.

    An owned Broker wait can emit a provisional result and then restart the same
    session for the actual completion.  Both lifecycles must be same-session,
    same-model genuine successes, and the second must carry the structural task
    notification that proves it is a real continuation rather than a replay.
    Only the final text is returned, and it stays a provisional observation.
    """
    _need(role in TOOLS and stream_filename in {"stream.jsonl", "qoder.stdout", "codex.stdout"}, "QODER_STREAM_NAME")
    run_dir = Path(run_dir).resolve(strict=True)
    events = _stream_events(run_dir, stream_filename)
    initial = [index for index, event in enumerate(events)
               if event.get("type") == "system" and event.get("subtype") == "init"]
    terminal = [index for index, event in enumerate(events) if event.get("type") == "result"]
    _need(len(initial) == len(terminal) == 2 and initial[0] == 0 and terminal[1] == len(events) - 1
          and initial[0] < terminal[0] < initial[1] < terminal[1], "QODER_OWNED_WAIT_CARDINALITY")
    session = _session_id(events[initial[0]])
    for index in initial:
        _need(events[index].get("session_id") == session, "QODER_CROSS_SESSION")
        _init_gates(events[index], session, expected_model, role)
    for event in events:
        _event_gates(event, session, role)
    _need(any(_task_notification(event) for event in events[initial[1] + 1:terminal[1]]),
          "QODER_OWNED_WAIT_NOTIFICATION")
    _result_gates(events[terminal[0]], session)
    return {"session_id": session, "text": _result_gates(events[terminal[1]], session),
            "segments": 2}


def finalize_qoder_output(run_dir: Path, *, stream_filename: str = "stream.jsonl",
                          expected_model: str | None = None, role: str = "coordinator",
                          write_result: bool = True) -> dict:
    _need(type(write_result) is bool, "QODER_WRITE_POLICY")
    terminal = validate_qoder_terminal(run_dir, stream_filename=stream_filename,
                                       expected_model=expected_model, role=role)
    run_dir = Path(run_dir).resolve(strict=True)
    try:
        value = _json(terminal["text"])
    except QoderError as error:
        if type(error.__cause__) is json.JSONDecodeError:
            raise QoderTerminalBodyError("QODER_TERMINAL_BODY_JSON") from error
        raise
    if type(value) is not dict:
        raise QoderTerminalBodyError("QODER_TERMINAL_BODY_OBJECT")
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    _need(len(payload) <= MAX_RESULT_BYTES, "QODER_RESULT_BOUND")
    if not write_result:
        return value
    _need(not os.path.lexists(run_dir / "result.json"), "QODER_RESULT_EXISTS")
    descriptor = os.open(run_dir / "result.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(payload)
        while view:
            count = os.write(descriptor, view); _need(count > 0, "QODER_RESULT_SHORT_WRITE"); view = view[count:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    descriptor = os.open(run_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return value
