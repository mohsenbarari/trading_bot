"""Finite recovery of a provably stopped, tool-free maintenance author.

This module never runs a model, adopts output, changes Source or releases a
reservation. The trusted caller retains the normal maintenance locks/authority.
An unknown/live process or a completed capture requires reconciliation instead.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

KIND = "QODER_TOOL_FREE_AUTHOR_INTERRUPTION_RECOVERY_V1"
MAINTENANCE = "QODER_AUTOMATION_MAINTENANCE_V1"
CUSTODY = "author-process.json"
RECEIPT = "author-recovery-1.json"
ORIGIN_RUN = re.compile(r"maint-[0-9a-f]{32}-worker-[0-2]-qa")
RUN = re.compile(r"maint-[0-9a-f]{32}-worker-[0-2]-qa(?:-recovery-1)?")
SHA = re.compile(r"[0-9a-f]{64}")
HEAD = re.compile(r"[0-9a-f]{40}")
SUCCESS = {"result.json", "delta-result.json", "generation-completion.json",
           "qoder-author-completion.json"}
FILES = {"task.json", "status.json", "prompt.txt", "response-schema.json",
         "stream.jsonl", "stderr.txt", CUSTODY}
MAX_SNAPSHOT = 1_000_000  # Explicit maintenance envelope; not business Task authority.
MAX_STREAM = 16_000_000


class AuthorResumeError(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise AuthorResumeError(code)


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _directory(path):
    path = Path(path)
    need(path.is_absolute() and path.resolve(strict=True) == path, "AUTHOR_RESUME_DIRECTORY_ALIAS")
    info = path.stat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
         and stat.S_IMODE(info.st_mode) == 0o700, "AUTHOR_RESUME_DIRECTORY_CUSTODY")
    return path


def _file(path, maximum, *, body=False):
    need(path.resolve(strict=True) == path, "AUTHOR_RESUME_FILE_ALIAS")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
             and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600
             and before.st_size <= maximum, "AUTHOR_RESUME_FILE_CUSTODY")
        h = hashlib.sha256(); count = 0; chunks = []
        while True:
            block = os.read(fd, min(65536, maximum + 1 - count))
            if not block:
                break
            count += len(block); need(count <= maximum, "AUTHOR_RESUME_FILE_BOUND")
            h.update(block)
            if body:
                chunks.append(block)
        keys = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        after = os.fstat(fd); current = path.lstat()
        need(count == before.st_size and all(getattr(before, k) == getattr(after, k)
             == getattr(current, k) for k in keys), "AUTHOR_RESUME_FILE_CHANGED")
        return {"bytes": count, "sha256": h.hexdigest()}, b"".join(chunks) if body else None
    finally:
        os.close(fd)


def _json(path, maximum=MAX_SNAPSHOT):
    _, raw = _file(path, maximum, body=True)
    def pairs(items):
        value = {}
        for key, item in items:
            need(key not in value, "AUTHOR_RESUME_DUPLICATE_KEY"); value[key] = item
        return value
    def invalid(_):
        raise AuthorResumeError("AUTHOR_RESUME_NONFINITE")
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, AuthorResumeError):
            raise
        raise AuthorResumeError("AUTHOR_RESUME_INVALID_JSON") from None
    need(type(value) is dict, "AUTHOR_RESUME_OBJECT_REQUIRED")
    return value


def _new(path, value):
    raw = encode(value)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(raw)
        while view:
            n = os.write(fd, view); need(n > 0, "AUTHOR_RESUME_SHORT_WRITE"); view = view[n:]
        os.fsync(fd)
    finally:
        os.close(fd)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _process(pid):
    need(type(pid) is int and pid > 1, "AUTHOR_RESUME_PID")
    path = Path("/proc") / str(pid) / "stat"
    try:
        info = path.stat()
        need(info.st_uid == os.geteuid(), "AUTHOR_RESUME_PROCESS_OWNER")
        with path.open("rb") as stream:
            raw = stream.read(4097)
    except FileNotFoundError:
        return None
    need(0 < len(raw) <= 4096 and b")" in raw, "AUTHOR_RESUME_PROCESS_STAT")
    fields = raw[raw.rfind(b")") + 2:].split()
    need(len(fields) >= 20, "AUTHOR_RESUME_PROCESS_STAT")
    return {"pid": pid, "pgid": int(fields[2]), "start_ticks": int(fields[19])}


def _bindings(directory, source_head, task_sha256, authorization_sha256):
    need(type(source_head) is str and HEAD.fullmatch(source_head)
         and all(type(v) is str and SHA.fullmatch(v) for v in (task_sha256, authorization_sha256)),
         "AUTHOR_RESUME_BINDING_SHAPE")
    material = _json(directory / "task.json"); status = _json(directory / "status.json")
    auth = material.get("author_authorization")
    need(type(auth) is dict and auth.get("kind") == MAINTENANCE
         and type(material.get("sources")) is dict, "AUTHOR_RESUME_MAINTENANCE_ONLY")
    recovery = material.get("author_recovery")
    need((recovery is None and ORIGIN_RUN.fullmatch(directory.name))
         or (type(recovery) is dict and directory.name.endswith("-recovery-1")),
         "AUTHOR_RESUME_VARIANT")
    need(material.get("source_head") == source_head and status.get("head") == source_head
         and status.get("run_id") == directory.name and RUN.fullmatch(directory.name)
         and type(material.get("task")) is dict
         and status.get("task_id") == material["task"].get("task_id")
         and status.get("role") == "worker" and status.get("author_provider") == "qoder"
         and status.get("authority") == "PROPOSAL_ONLY_NOT_ACCEPTANCE"
         and status.get("completion_policy") == "QODER_PROPOSAL_AUTHOR_COMPLETION_V1"
         and status.get("state") in {"RUNNING", "BLOCKED"}, "AUTHOR_RESUME_STATUS")
    if status["state"] == "BLOCKED":
        error = status.get("error")
        need(type(error) is str and error.startswith(("TimeoutExpired: ", "InterruptedError: ",
                                                    "KeyboardInterrupt: "))
             and not status.get("quota_evidence"), "AUTHOR_RESUME_NOT_INTERRUPTION")
    need(digest(encode(material["task"])) == task_sha256
         and digest(encode(auth)) == authorization_sha256, "AUTHOR_RESUME_TASK_OR_AUTH_CHANGED")
    if recovery is not None:
        root = directory.parents[3]
        validate_author_recovery(root, recovery, task=material["task"], sources=material["sources"],
                                 source_head=source_head, authorization=auth,
                                 run_id=directory.name)
    return material, status


def _tool_free_command(command):
    """Validate the exact tool-free Qoder suffix inside the bwrap command."""
    need(type(command) is list and all(type(item) is str for item in command),
         "AUTHOR_RESUME_COMMAND_SHAPE")
    separators = [index for index, item in enumerate(command) if item == "--"]
    need(len(separators) <= 1, "AUTHOR_RESUME_COMMAND_WRAPPER")
    argv = command if not separators else command[separators[0] + 1:]
    need(len(argv) in {24, 26} and Path(argv[0]).is_absolute(),
         "AUTHOR_RESUME_COMMAND_SHAPE")
    if len(argv) == 26:
        need(argv[13] == "--max-model-request-retries" and argv[14].isdigit()
             and 0 <= int(argv[14]) <= 2, "AUTHOR_RESUME_MODEL_RETRIES")
        argv = argv[:13] + argv[15:]
    positions = {1: "--settings", 3: "--append-system-prompt", 5: "--cwd",
        7: "--model", 9: "--reasoning-effort",
        13: "--output-format", 15: "--no-session-persistence",
        16: "--permission-mode", 18: "--strict-mcp-config", 19: "--mcp-config",
        21: "--tools", 23: "--print"}
    need(all(argv[index] == flag for index, flag in positions.items())
         and argv[4] and Path(argv[6]).is_absolute() and argv[8]
         and argv[10] in {"low", "medium", "high", "xhigh", "max"}
         and argv[11] in {"--thinking", "--thinking-budget"}
         and ((argv[11] == "--thinking" and argv[12] == "adaptive")
              or (argv[11] == "--thinking-budget" and argv[12].isdigit()
                  and 1024 <= int(argv[12]) <= 1048576))
         and argv[14] == "stream-json"
         and argv[17] == "auto" and argv[20] == '{"mcpServers":{}}'
         and argv[22] == "", "AUTHOR_RESUME_NOT_TOOL_FREE")
    try:
        settings = json.loads(argv[2])
    except (ValueError, UnicodeError, RecursionError):
        raise AuthorResumeError("AUTHOR_RESUME_SETTINGS") from None
    allowed = {"general", "modelConfigs"}
    need(type(settings) is dict and set(settings) <= allowed
         and settings.get("general") == {"enableAutoUpdate": False},
         "AUTHOR_RESUME_SETTINGS")
    if "modelConfigs" in settings:
        model_configs = settings["modelConfigs"]
        need(type(model_configs) is dict and set(model_configs) == {"customOverrides"}
             and type(model_configs["customOverrides"]) is list
             and len(model_configs["customOverrides"]) == 1
             and type(model_configs["customOverrides"][0]) is dict,
             "AUTHOR_RESUME_SETTINGS")
        override = model_configs["customOverrides"][0]
        need(set(override) == {"match", "modelConfig"}
             and type(override["match"]) is dict
             and type(override["modelConfig"]) is dict,
             "AUTHOR_RESUME_SETTINGS")
        generation = override["modelConfig"].get("generateContentConfig")
        need(set(override["modelConfig"]) == {"generateContentConfig"}
             and type(generation) is dict and set(generation) == {"maxOutputTokens"},
             "AUTHOR_RESUME_SETTINGS")
        maximum = generation["maxOutputTokens"]
        expected = {"customOverrides": [{"match": {"model": argv[8], "overrideScope": "core"},
            "modelConfig": {"generateContentConfig": {"maxOutputTokens": maximum}}}]}
        need(type(maximum) is int and not isinstance(maximum, bool)
             and 32768 <= maximum <= 1048576 and settings["modelConfigs"] == expected,
             "AUTHOR_RESUME_SETTINGS")
        if argv[11] == "--thinking-budget":
            need(int(argv[12]) < maximum, "AUTHOR_RESUME_SETTINGS")
    return argv


def persist_author_process(run_dir, process, *, source_head, task_sha256,
                           authorization_sha256, command_sha256):
    """Call after Popen(start_new_session=True), BEFORE writing any prompt input."""
    directory = _directory(run_dir)
    _bindings(directory, source_head, task_sha256, authorization_sha256)
    command = process.args
    need(type(command_sha256) is str and SHA.fullmatch(command_sha256), "AUTHOR_RESUME_COMMAND_SHA")
    _tool_free_command(command)
    need(digest(encode(command)) == command_sha256, "AUTHOR_RESUME_COMMAND_BINDING")
    owner = _process(process.pid)
    need(owner is not None and owner["pgid"] == owner["pid"], "AUTHOR_RESUME_NO_OWNED_GROUP")
    value = {"kind": KIND, "run_id": directory.name, "source_head": source_head,
        "task_sha256": task_sha256, "authorization_sha256": authorization_sha256,
        "task_snapshot_sha256": _file(directory / "task.json", MAX_SNAPSHOT)[0]["sha256"],
        "command_sha256": command_sha256, "owner": owner}
    _new(directory / CUSTODY, value)
    return value


def _gone(owner):
    need(type(owner) is dict and set(owner) == {"pid", "pgid", "start_ticks"}
         and all(type(v) is int and v > 1 for v in owner.values())
         and owner["pid"] == owner["pgid"], "AUTHOR_RESUME_OWNER_SCHEMA")
    live = _process(owner["pid"])
    if live is not None:
        need(live["start_ticks"] == owner["start_ticks"], "AUTHOR_RESUME_PROCESS_REUSED")
        raise AuthorResumeError("AUTHOR_RESUME_PROCESS_LIVE_OR_REUSED")
    try:
        os.killpg(owner["pgid"], 0)
    except ProcessLookupError:
        return
    except PermissionError:
        raise AuthorResumeError("AUTHOR_RESUME_GROUP_UNKNOWN") from None
    raise AuthorResumeError("AUTHOR_RESUME_GROUP_LIVE")


def _interrupted_stream(directory):
    """Retained SDK success/error is reconciliation authority, never retry input."""
    stderr = directory / "stderr.txt"
    if os.path.lexists(stderr):
        need(_file(stderr, MAX_STREAM)[0]["bytes"] == 0,
             "AUTHOR_RESUME_DIAGNOSTIC_REQUIRES_RECONCILIATION")
    path = directory / "stream.jsonl"
    if not os.path.lexists(path):
        return  # The custody gate precedes the first stdin write/output.
    _, raw = _file(path, MAX_STREAM, body=True)
    lines = raw.splitlines(keepends=True)
    need(len(lines) <= 16384, "AUTHOR_RESUME_STREAM_EVENT_BOUND")
    def pairs(items):
        value = {}
        for key, item in items:
            need(key not in value, "AUTHOR_RESUME_DUPLICATE_EVENT_KEY"); value[key] = item
        return value
    def invalid(_):
        raise AuthorResumeError("AUTHOR_RESUME_NONFINITE_EVENT")
    for index, line in enumerate(lines):
        need(len(line) <= MAX_SNAPSHOT, "AUTHOR_RESUME_STREAM_EVENT_BOUND")
        if not line.strip():
            continue
        try:
            event = json.loads(line, object_pairs_hook=pairs, parse_constant=invalid)
        except AuthorResumeError:
            raise
        except (ValueError, UnicodeError, RecursionError):
            # A partial final record is unaccepted interrupted output. A complete
            # malformed record could conceal a provider failure and is ambiguous.
            need(index == len(lines) - 1 and not line.endswith(b"\n"),
                 "AUTHOR_RESUME_MALFORMED_COMPLETE_EVENT")
            continue
        need(type(event) is dict, "AUTHOR_RESUME_UNKNOWN_EVENT")
        need(event.get("type") != "result", "AUTHOR_RESUME_TERMINAL_REQUIRES_RECONCILIATION")
        need(event.get("type") not in {"error", "tool_error"}
             and event.get("subtype") not in {"error", "error_during_execution", "error_max_turns"}
             and not event.get("error"), "AUTHOR_RESUME_PROVIDER_FAILURE")
        need(event.get("type") in {"system", "assistant", "user", "stream_event"},
             "AUTHOR_RESUME_UNKNOWN_EVENT")
        message = event.get("message")
        need(event.get("stop_reason") != "max_tokens"
             and not (type(message) is dict and message.get("stop_reason") == "max_tokens"),
             "AUTHOR_RESUME_OUTPUT_BUDGET_REQUIRES_RECONCILIATION")
        if type(message) is dict and type(message.get("content")) is list:
            need(not any(type(block) is dict and block.get("type") in {"tool_use", "tool_result"}
                         for block in message["content"]), "AUTHOR_RESUME_TOOL_EVENT")
        nested = event.get("event")
        if type(nested) is dict:
            need(nested.get("type") not in {"error", "tool_error", "result"}
                 and not nested.get("error"), "AUTHOR_RESUME_PROVIDER_FAILURE")
            delta = nested.get("delta")
            need(nested.get("stop_reason") != "max_tokens"
                 and not (type(delta) is dict and delta.get("stop_reason") == "max_tokens"),
                 "AUTHOR_RESUME_OUTPUT_BUDGET_REQUIRES_RECONCILIATION")
            block = nested.get("content_block")
            need(not (type(block) is dict and block.get("type") in {"tool_use", "tool_result"}),
                 "AUTHOR_RESUME_TOOL_EVENT")


def _recovery(directory, source_head, task_sha256, authorization_sha256):
    material, status = _bindings(directory, source_head, task_sha256, authorization_sha256)
    need(not any(os.path.lexists(directory / name) for name in SUCCESS), "AUTHOR_RESUME_EXISTING_RESULT")
    need(not os.path.lexists(directory / "capture.json"), "AUTHOR_RESUME_CAPTURE_REQUIRES_RECONCILIATION")
    need(not any(status.get(k) is not None for k in ("result_sha256", "completion_proof_sha256",
                                                   "conversation_id")), "AUTHOR_RESUME_COMPLETION_METADATA")
    owner = _json(directory / CUSTODY, 8192)
    need(set(owner) == {"kind", "run_id", "source_head", "task_sha256", "authorization_sha256",
                       "task_snapshot_sha256", "command_sha256", "owner"}
         and owner["kind"] == KIND and owner["run_id"] == directory.name
         and owner["source_head"] == source_head and owner["task_sha256"] == task_sha256
         and owner["authorization_sha256"] == authorization_sha256
         and type(owner["command_sha256"]) is str and SHA.fullmatch(owner["command_sha256"])
         and owner["task_snapshot_sha256"] == _file(directory / "task.json", MAX_SNAPSHOT)[0]["sha256"],
         "AUTHOR_RESUME_CUSTODY_CHANGED")
    _gone(owner["owner"])
    _interrupted_stream(directory)
    artifacts = {}
    for path in sorted(directory.iterdir()):
        if path.name == RECEIPT:
            continue
        need(path.name in FILES, "AUTHOR_RESUME_UNKNOWN_ARTIFACT")
        artifacts[path.name] = _file(path, MAX_STREAM if path.name in {"stream.jsonl", "stderr.txt"}
                                    else MAX_SNAPSHOT)[0]
    _gone(owner["owner"])
    return {"kind": KIND, "attempt": 1, "origin_run_id": directory.name,
        "recovery_run_id": directory.name + "-recovery-1", "source_head": source_head,
        "task_sha256": task_sha256, "authorization_sha256": authorization_sha256,
        "task_snapshot_sha256": owner["task_snapshot_sha256"], "artifacts": artifacts,
        "decision": "DISCARD_UNACCEPTED_OUTPUT_NEW_SESSION"}


def prepare_maintenance_author_recovery(root, run_dir, *, source_head, task_sha256,
                                        authorization_sha256):
    """Reserve ONE retained same-job variant; never run it or touch old artifacts."""
    root = Path(root).resolve(strict=True); directory = _directory(run_dir)
    need(directory.parent == root / ".local/two-site-refactor/proposals", "AUTHOR_RESUME_RUN_PATH")
    value = _recovery(directory, source_head, task_sha256, authorization_sha256)
    path = directory / RECEIPT
    if os.path.lexists(path):
        need(_json(path) == value and _file(path, MAX_SNAPSHOT, body=True)[1] == encode(value),
             "AUTHOR_RESUME_RECEIPT_CHANGED")
    else:
        _new(path, value)
    return value


def validate_author_recovery(root, receipt, *, task, sources, source_head, authorization,
                             run_id):
    """Call both before the fresh variant and during archived completion replay."""
    root = Path(root).resolve(strict=True)
    need(type(receipt) is dict and type(receipt.get("origin_run_id")) is str
         and ORIGIN_RUN.fullmatch(receipt["origin_run_id"]), "AUTHOR_RESUME_RECEIPT_SHAPE")
    directory = _directory(root / ".local/two-site-refactor/proposals" / receipt["origin_run_id"])
    expected = _recovery(directory, source_head, digest(encode(task)), digest(encode(authorization)))
    need(encode(receipt) == encode(expected) and run_id == expected["recovery_run_id"]
         and _file(directory / RECEIPT, MAX_SNAPSHOT, body=True)[1] == encode(expected),
         "AUTHOR_RESUME_RECEIPT_BINDING")
    material = _json(directory / "task.json")
    need(encode(material["sources"]) == encode(sources), "AUTHOR_RESUME_SOURCE_CHANGED")
    return receipt
