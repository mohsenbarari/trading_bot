#!/usr/bin/env python3
"""Bounded Antigravity proposals. Only Codex applies/reviews/commits them.

The controller writes ignored run artifacts only; it does not apply generated code,
change Git refs/Stage state or deploy. The CLI uses plan/sandbox mode and a deny hook;
tool attempts invalidate its result. Each invocation is a fresh session.
"""
from __future__ import annotations

import argparse
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
MODEL = "gemini-3.8-flash-high"
MAX_CONTEXT = 160_000
MAX_OUTPUT = 2_000_000
STREAM_CHUNK_SIZE = 64 * 1024
EXPECTED_CLI_VERSION = "1.1.27"
EXPECTED_CLI_SHA256 = "93eb2118b778a4005700b54cdd7e08b896fbe665d5ff338e38e9e53da9a091ea"
DENY_HOOK = {"proposal-only": {"PreToolUse": [{"matcher": "*", "hooks": [{
    "type": "command", "timeout": 5,
    "command": "/usr/bin/printf '%s\\n' '{\"decision\":\"deny\",\"reason\":\"Proposal-only session: tools are disabled. Return JSON directly from supplied context.\"}'",
}]}]}}
SECRET = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"\d{8,12}:[A-Za-z0-9_-]{30,})\b"
)
PROTECTED = {".git", ".local", ".agents", ".codex", ".cursor", "data", "logs",
             "uploads", "secrets", "credentials", "backups"}
CANONICAL_RESPONSE_KEYS = frozenset({
    "status", "summary", "covered_requirements", "findings", "changes",
})


class ProposalError(ValueError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_fingerprint(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns, value.st_nlink,
    )


def bounded_file_bytes(root: Path, path: Path, limit: int, label: str) -> bytes:
    """Read one project file without following links or allocating past limit + 1."""
    if limit < 0:
        raise ProposalError("file byte limit cannot be negative")
    root = root.resolve(strict=True)
    candidate = path if path.is_absolute() else root / path
    candidate = Path(os.path.abspath(candidate))
    if not candidate.is_relative_to(root):
        raise ProposalError(f"{label} is outside the project root")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProposalError(f"cannot safely resolve {label}") from exc
    if resolved != candidate:
        raise ProposalError(f"{label} is a symlink or path escape")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as exc:
        raise ProposalError(f"cannot safely open {label}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ProposalError(f"{label} is not a regular file")
        # A hardlink can make bytes outside the project reachable through an
        # apparently safe in-project pathname. Proposal inputs must be unique.
        if before.st_nlink != 1:
            raise ProposalError(f"{label} is hardlinked and cannot be trusted")
        if before.st_size > limit:
            raise ProposalError(f"{label} exceeds the safe context budget")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            data = source.read(limit + 1)
        if len(data) > limit:
            raise ProposalError(f"{label} exceeds the safe context budget")
        after = os.fstat(descriptor)
        try:
            named = os.stat(candidate, follow_symlinks=False)
            final_resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProposalError(f"{label} changed while it was being read") from exc
        if (file_fingerprint(before) != file_fingerprint(after)
                or file_fingerprint(after) != file_fingerprint(named)
                or final_resolved != candidate):
            raise ProposalError(f"{label} changed while it was being read")
        return data
    finally:
        os.close(descriptor)


def bounded_file_text(root: Path, path: Path, limit: int, label: str) -> str:
    return bounded_file_bytes(root, path, limit, label).decode("utf-8")


def acquire_proposal_lock(root: Path) -> tuple[int, Path]:
    """Acquire the one nonblocking proposal-generation lock for this worktree."""
    local = root / ".local/two-site-refactor/proposals"
    if local.resolve() != local:
        raise ProposalError("run storage cannot be a symlink")
    local.mkdir(parents=True, exist_ok=True)
    if local.resolve(strict=True) != local:
        raise ProposalError("run storage cannot be a symlink")
    lock_path = local / ".runner.lock"
    flags = (os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ProposalError("cannot safely open proposal runner lock") from exc
    try:
        lock_stat = os.fstat(descriptor)
        if (not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1
                or lock_stat.st_uid != os.geteuid()):
            raise ProposalError("proposal runner lock is not a trusted regular file")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProposalError("another proposal runner is already active") from exc
        return descriptor, local
    except BaseException:
        os.close(descriptor)
        raise


def release_proposal_lock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


def capture_bounded_process(
    process: subprocess.Popen,
    input_bytes: bytes,
    stdout_path: Path,
    stderr_path: Path,
    timeout: float,
    max_output: int = MAX_OUTPUT,
) -> None:
    """Drain all pipes concurrently while enforcing one hard stdout+stderr cap."""
    if max_output < 1 or process.stdin is None or process.stdout is None or process.stderr is None:
        raise ProposalError("invalid bounded process configuration")
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    input_offset = 0
    total_output = 0
    streams = {}

    try:
        with stdout_path.open("xb") as stdout_file, stderr_path.open("xb") as stderr_file:
            streams = {process.stdout: stdout_file, process.stderr: stderr_file}
            for stream in streams:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, "output")
            os.set_blocking(process.stdin.fileno(), False)
            if input_bytes:
                selector.register(process.stdin, selectors.EVENT_WRITE, "input")
            else:
                process.stdin.close()

            while selector.get_map():
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    raise subprocess.TimeoutExpired(getattr(process, "args", "CLI"), timeout)
                events = selector.select(min(remaining_time, 0.25))
                if not events and process.poll() is not None:
                    try:
                        selector.get_key(process.stdin)
                    except KeyError:
                        pass
                    else:
                        selector.unregister(process.stdin)
                        process.stdin.close()
                        continue
                for key, _mask in events:
                    stream = key.fileobj
                    if key.data == "input":
                        try:
                            written = os.write(stream.fileno(), input_bytes[input_offset:])
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            selector.unregister(stream)
                            stream.close()
                            continue
                        input_offset += written
                        if input_offset == len(input_bytes):
                            selector.unregister(stream)
                            stream.close()
                        continue

                    remaining_output = max_output - total_output
                    try:
                        chunk = os.read(
                            stream.fileno(),
                            min(STREAM_CHUNK_SIZE, remaining_output + 1),
                        )
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    if len(chunk) > remaining_output:
                        if remaining_output:
                            streams[stream].write(chunk[:remaining_output])
                            total_output += remaining_output
                        raise ProposalError("CLI output exceeds budget")
                    streams[stream].write(chunk)
                    total_output += len(chunk)

            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                raise subprocess.TimeoutExpired(getattr(process, "args", "CLI"), timeout)
            process.wait(timeout=remaining_time)
    except BaseException:
        terminate_process(process)
        raise
    finally:
        selector.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def cli_identity(cli: Path) -> dict[str, str]:
    cli = cli.resolve(strict=True)
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise ProposalError("CLI must be an executable regular file")
    digest = file_sha256(cli)
    if digest != EXPECTED_CLI_SHA256:
        raise ProposalError("CLI binary hash is not the reviewed pin")
    with tempfile.TemporaryDirectory(prefix="two-site-cli-version-") as temporary:
        temporary_path = Path(temporary)
        with subprocess.Popen(
            [str(cli), "--version"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        ) as process:
            capture_bounded_process(
                process,
                b"",
                temporary_path / "stdout",
                temporary_path / "stderr",
                10,
                max_output=4096,
            )
        version_text = bounded_file_text(
            temporary_path, temporary_path / "stdout", 4096, "CLI version output"
        ).strip()
        stderr_text = bounded_file_text(
            temporary_path, temporary_path / "stderr", 4096, "CLI version error output"
        ).strip()
        if process.returncode or stderr_text or version_text != EXPECTED_CLI_VERSION:
            raise ProposalError("CLI version is not the reviewed pin")
    if file_sha256(cli) != digest:
        raise ProposalError("CLI identity changed during version inspection")
    return {"path": str(cli), "version": version_text, "sha256": digest}


def unique_json(text: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProposalError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs)


def relative_file(root: Path, name: str) -> Path:
    if (not isinstance(name, str) or not name or "\\" in name or "\0" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise ProposalError("noncanonical file path")
    path = PurePosixPath(name)
    if (path.is_absolute() or any(part in PROTECTED for part in path.parts)
            or any(part.startswith(".env") and not part.endswith(".example") for part in path.parts)
            or path.suffix.lower() not in {".py", ".md", ".json", ".yaml", ".yml", ".ts", ".vue", ".css", ".example"}):
        raise ProposalError("file outside source/document scope")
    target = root / name
    if target.resolve() != target or not target.is_relative_to(root):
        raise ProposalError("symlink or path escape")
    return target


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["/usr/bin/git", "--no-replace-objects", "-C", str(root), *args],
        text=True, timeout=20, stderr=subprocess.DEVNULL,
    ).strip()


def load_task(root: Path, task_path: Path) -> tuple[dict, dict]:
    raw = bounded_file_bytes(root, task_path, MAX_CONTEXT, "task")
    task = unique_json(raw.decode())
    required = {"task_id", "goal", "context_files", "allowed_files", "requirements", "test_selectors"}
    if not isinstance(task, dict) or set(task) != required:
        raise ProposalError("task keys must match the documented contract")
    if (not isinstance(task["task_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", task["task_id"])):
        raise ProposalError("invalid task ID")
    if not isinstance(task["goal"], str) or not task["goal"].strip():
        raise ProposalError("task goal is missing")
    for key in ("context_files", "allowed_files", "requirements", "test_selectors"):
        values = task[key]
        if (not isinstance(values, list) or not values or len(values) > 32
                or any(not isinstance(x, str) or not x.strip() for x in values)
                or len(set(values)) != len(values)):
            raise ProposalError("task lists must contain unique bounded nonempty strings")
    sources = {}
    for name in dict.fromkeys(task["context_files"] + task["allowed_files"]):
        target = relative_file(root, name)
        if not target.exists():
            if name in task["context_files"]:
                raise ProposalError("required context file is missing: " + name)
            sources[name] = {"sha256": None, "content": None}
            continue
        content = bounded_file_bytes(root, target, MAX_CONTEXT, "context file " + name)
        if SECRET.search(content.decode()):
            raise ProposalError("credential-like content excluded from model input")
        sources[name] = {"sha256": sha(content), "content": content.decode()}
    if len(json.dumps({"task": task, "sources": sources}).encode()) > MAX_CONTEXT:
        raise ProposalError("split the task: supplied context exceeds budget")
    return task, sources


def schema(role: str) -> dict:
    fields = {
        "status": {"type": "string", "enum": ["PROPOSAL_READY", "BLOCKED"] if role == "worker" else ["VERIFIED", "CHANGES_REQUIRED", "BLOCKED"]},
        "summary": {"type": "string"},
        "covered_requirements": {"type": "array", "items": {"type": "integer"}},
        "findings": {"type": "array", "items": {"type": "string"}},
        "changes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["path", "original_sha256", "content"],
            "properties": {"path": {"type": "string"},
                           "original_sha256": {"type": ["string", "null"]},
                           "content": {"type": "string"}},
        }},
    }
    return {"type": "object", "additionalProperties": False,
            "required": list(fields), "properties": fields}


def validate_result(result: dict, task: dict, sources: dict, role: str) -> dict:
    if not isinstance(result, dict) or set(result) != set(schema(role)["required"]):
        raise ProposalError("incomplete response contract")
    statuses = schema(role)["properties"]["status"]["enum"]
    if result["status"] not in statuses or not isinstance(result["summary"], str) or not result["summary"].strip():
        raise ProposalError("missing status or explanation")
    for field in ("findings", "covered_requirements", "changes"):
        if not isinstance(result[field], list):
            raise ProposalError("malformed response list")
    if any(not isinstance(x, str) or not x.strip() for x in result["findings"]):
        raise ProposalError("findings must be explicit strings")
    coverage = result["covered_requirements"]
    if any(type(x) is not int for x in coverage) or len(set(coverage)) != len(coverage):
        raise ProposalError("invalid requirement coverage")
    success = result["status"] in {"PROPOSAL_READY", "VERIFIED"}
    if success and (set(coverage) != set(range(1, len(task["requirements"]) + 1)) or result["findings"]):
        raise ProposalError("success omits requirements or has unresolved findings")
    if not success and (not result["findings"] or result["changes"]):
        raise ProposalError("blocked response needs findings and no partial changes")
    if role == "verifier" and result["changes"]:
        raise ProposalError("verifier cannot author changes")
    if role == "worker" and success and not result["changes"]:
        raise ProposalError("plan-only or no-op response is not implementation")
    seen = set()
    for change in result["changes"]:
        if not isinstance(change, dict) or set(change) != {"path", "original_sha256", "content"}:
            raise ProposalError("invalid change shape")
        name = change["path"]
        if not isinstance(name, str) or name not in task["allowed_files"] or name in seen:
            raise ProposalError("changed path is duplicated or outside assignment")
        seen.add(name)
        if change["original_sha256"] != sources[name]["sha256"]:
            raise ProposalError("proposal is based on different source bytes")
        if (not isinstance(change["content"], str) or not change["content"].strip()
                or change["content"] == sources[name]["content"]):
            raise ProposalError("empty or unchanged file")
        if SECRET.search(change["content"]) or "\0" in change["content"]:
            raise ProposalError("unsafe generated content")
    return result


def load_prior_worker(
    root: Path,
    proposal_path: Path,
    proposal_text: str,
    task: dict,
    sources: dict,
    head: str,
) -> tuple[dict, dict, str]:
    """Bind a review/correction to one complete worker run and exact proposal bytes."""
    baseline = unique_json(bounded_file_text(
        root, proposal_path.with_name("task.json"), 2 * MAX_CONTEXT, "worker snapshot"
    ))
    worker_status = unique_json(bounded_file_text(
        root, proposal_path.with_name("status.json"), MAX_CONTEXT, "worker status"
    ))
    proposal_sha256 = sha(proposal_text.encode("utf-8"))
    if (not isinstance(baseline, dict) or baseline.get("task") != task
            or baseline.get("source_head") != head or baseline.get("sources") != sources):
        raise ProposalError("verify before applying: worker baseline differs from current source/task")
    if (not isinstance(worker_status, dict)
            or worker_status.get("role") != "worker"
            or worker_status.get("state") != "PROPOSAL_READY"
            or worker_status.get("result_sha256") != proposal_sha256
            or not isinstance(worker_status.get("run_id"), str)
            or not isinstance(worker_status.get("conversation_id"), str)
            or not worker_status["conversation_id"].strip()):
        raise ProposalError("proposal is not bound to one completed worker run")
    candidate = validate_result(unique_json(proposal_text), task, sources, "worker")
    if candidate["status"] != "PROPOSAL_READY":
        raise ProposalError("review/correction requires a complete worker proposal")
    return candidate, worker_status, proposal_sha256


def validate_correction_feedback(text: str, proposal_sha256: str) -> dict:
    feedback = unique_json(text)
    required = {"reviewer", "reviewed_proposal_sha256", "findings", "test_evidence"}
    if not isinstance(feedback, dict) or set(feedback) != required:
        raise ProposalError("correction feedback does not match the exact contract")
    if feedback["reviewer"] not in {"gemini", "codex"}:
        raise ProposalError("correction feedback reviewer is invalid")
    if feedback["reviewed_proposal_sha256"] != proposal_sha256:
        raise ProposalError("correction feedback targets a different proposal")
    for field, allow_empty in (("findings", False), ("test_evidence", True)):
        values = feedback[field]
        if (not isinstance(values, list) or len(values) > 32
                or (not allow_empty and not values)
                or any(not isinstance(value, str) or not value.strip() for value in values)):
            raise ProposalError(f"correction feedback {field} is invalid")
    return feedback


def parse_stream(raw: str, task: dict, sources: dict, role: str) -> tuple[dict, dict]:
    events = [unique_json(line) for line in raw.splitlines() if line.strip()]
    if any(not isinstance(e, dict) for e in events):
        raise ProposalError("CLI event must be an object")
    for event in events:
        kind = event.get("event")
        if kind in {"init", "result", "step_update"} and not isinstance(event.get(kind), dict):
            raise ProposalError("malformed CLI event envelope")
    init = [e for e in events if e.get("event") == "init"]
    terminal = [e["result"] for e in events if e.get("event") == "result"]
    if len(init) != 1 or init[0]["init"].get("model") != MODEL or len(terminal) != 1:
        raise ProposalError("missing unique model/session/result evidence")
    envelope = terminal[0]
    session = init[0].get("conversation_id")
    if (not isinstance(session, str) or not session.strip()
            or envelope.get("status") != "SUCCESS" or envelope.get("denied_actions")
            or envelope.get("conversation_id") != session):
        raise ProposalError("CLI did not complete one successful session")

    finish_submissions = 0
    for event in events:
        if "conversation_id" in event and event.get("conversation_id") != session:
            raise ProposalError("CLI did not complete one successful session")
        if (
            "tool_name" in event
            or "tool_info" in event
            or "subagent_info" in event
            or event.get("step_type") in {"tool", "subagent"}
        ):
            raise ProposalError("tool attempt invalidates a proposal-only run")
        if event.get("event") != "step_update":
            continue
        step = event["step_update"]
        step_cid = step.get("conversation_id")
        if "conversation_id" in step and step_cid != session:
            raise ProposalError("CLI did not complete one successful session")
        is_tool_or_subagent = (
            "tool_name" in step
            or "tool_info" in step
            or "subagent_info" in step
            or step.get("step_type") in {"tool", "subagent"}
        )
        if not is_tool_or_subagent:
            continue
        tool_info = step.get("tool_info")
        params = tool_info.get("parameters") if isinstance(tool_info, dict) else None
        is_finish_submission = (
            step.get("step_type") == "tool"
            and step.get("tool_name") == "finish"
            and "subagent_info" not in step
            and isinstance(tool_info, dict)
            and set(tool_info.keys()) == {"name", "parameters"}
            and tool_info.get("name") == "finish"
            and isinstance(params, dict)
            and set(params.keys()) == CANONICAL_RESPONSE_KEYS
            and (step_cid is None or step_cid == session)
            and (event.get("conversation_id") is None or event.get("conversation_id") == session)
        )
        if not is_finish_submission or finish_submissions >= 1:
            raise ProposalError("tool attempt invalidates a proposal-only run")
        finish_submissions += 1

    # With --json-schema the documented machine payload is structured_output.
    # CLI 1.1.27 may also append its submission object to free-text response.
    # Never scrape that text or fall back when a supplied structured value is bad.
    if "structured_output" in envelope:
        result = envelope["structured_output"]
    else:
        if not isinstance(envelope.get("response"), str):
            raise ProposalError("CLI result has no response text")
        result = unique_json(envelope["response"])
    return validate_result(result, task, sources, role), envelope


def proposal_diff(changes: list, sources: dict) -> str:
    patch = ""
    for change in changes:
        old = sources[change["path"]]["content"]
        for line in difflib.unified_diff(
            (old or "").splitlines(keepends=True), change["content"].splitlines(keepends=True),
            fromfile="a/" + change["path"] if old is not None else "/dev/null",
            tofile="b/" + change["path"],
        ):
            patch += line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
    return patch


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def run(root: Path, task_path: Path, role: str, run_id: str, timeout: int,
        feedback: Path | None = None, proposal: Path | None = None,
        cli: Path | None = None) -> Path:
    root = root.resolve(strict=True)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", run_id) or not 10 <= timeout <= 900:
        raise ProposalError("invalid run ID or timeout")
    if role not in {"worker", "verifier"}:
        raise ProposalError("invalid proposal role")
    task, sources = load_task(root, task_path)
    head = git(root, "rev-parse", "HEAD")
    initial_status = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    material = {"task": task, "source_head": head, "sources": sources}
    input_hashes = {}
    for key, path in (("feedback", feedback), ("proposal", proposal)):
        if path is not None:
            content = bounded_file_text(root, path, MAX_CONTEXT, key)
            if SECRET.search(content):
                raise ProposalError("credential-like content excluded from model input")
            material[key] = content
            input_hashes[key] = sha(content.encode("utf-8"))
    if role == "verifier" and proposal is None:
        raise ProposalError("verifier needs the exact proposal")
    if role == "verifier" and feedback is not None:
        raise ProposalError("first-pass Gemini verifier does not accept feedback")
    if role == "worker" and ((feedback is None) != (proposal is None)):
        raise ProposalError("correction worker requires both proposal and feedback")
    prior_status = None
    if proposal is not None:
        _candidate, prior_status, proposal_sha256 = load_prior_worker(
            root, proposal, material["proposal"], task, sources, head
        )
        if input_hashes["proposal"] != proposal_sha256:
            raise ProposalError("proposal hash changed during validation")
        if role == "worker":
            material["feedback"] = validate_correction_feedback(
                material["feedback"], proposal_sha256
            )
    prompt = bounded_file_text(
        ROOT, ROOT / "docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md",
        MAX_CONTEXT, "prompt template",
    )
    prompt += "\nROLE: " + role + "\nCONTEXT_JSON:\n" + json.dumps(material, ensure_ascii=False)
    if len(prompt.encode()) > 2 * MAX_CONTEXT:
        raise ProposalError("split task or feedback into a smaller coherent unit")
    if SECRET.search(prompt):
        raise ProposalError("credential-like content excluded from model input")
    if cli is None:
        local_cli = root / ".local/tools/agy"
        if local_cli.is_file():
            cli = local_cli
        else:
            common_dir = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
            cli = common_dir.parent / ".local/tools/agy"
    cli = cli.resolve(strict=True)
    lock_descriptor, local = acquire_proposal_lock(root)
    try:
        # The lock covers every CLI invocation, including identity probes.
        initial_cli_identity = cli_identity(cli)
        out = local / run_id
        out.mkdir(mode=0o700)  # Evidence is never overwritten by a retry.
        workspace = out / "workspace"
        (workspace / ".agents").mkdir(parents=True)
        write_json(workspace / ".agents/hooks.json", DENY_HOOK)
        write_json(out / "task.json", material)
        (out / "prompt.txt").write_text(prompt)
        meta = {"run_id": run_id, "task_id": task["task_id"], "role": role,
                "head": head, "model": MODEL, "state": "RUNNING", "started_at": time.time(),
                "retain_until": time.time() + 7 * 86400,
                "authority": "PROPOSAL_ONLY_NOT_ACCEPTANCE",
                "cli": initial_cli_identity}
        if prior_status is not None:
            meta.update(
                input_worker_run_id=prior_status["run_id"],
                input_worker_conversation_id=prior_status["conversation_id"],
                input_proposal_sha256=input_hashes["proposal"],
            )
        if feedback is not None:
            meta["input_feedback_sha256"] = input_hashes["feedback"]
        write_json(out / "status.json", meta)
        command = [str(cli), "--mode", "plan", "--sandbox", "--model", MODEL,
                   "--effort", "high", "--input-format", "stream-json",
                   "--output-format", "stream-json", "--print-timeout", f"{timeout}s",
                   "--json-schema", json.dumps(schema(role))]
        try:
            # Auth belongs to the CLI. No credential files are read or copied here.
            with subprocess.Popen(command, cwd=workspace, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  start_new_session=True) as process:
                meta["cli_pid"] = process.pid
                write_json(out / "status.json", meta)
                capture_bounded_process(
                    process,
                    (json.dumps({"event": "user", "message": {"content": prompt}}) + "\n").encode(),
                    out / "stream.jsonl",
                    out / "stderr.txt",
                    timeout + 15,
                )
            if process.returncode:
                raise ProposalError(f"CLI failed with exit {process.returncode}; inspect run evidence")
            final_cli_identity = cli_identity(cli)
            if final_cli_identity != initial_cli_identity:
                raise ProposalError("CLI identity changed during generation")
            raw = bounded_file_text(root, out / "stream.jsonl", MAX_OUTPUT, "CLI event stream")
            result, envelope = parse_stream(raw, task, sources, role)
            if (prior_status is not None
                    and envelope["conversation_id"] == prior_status["conversation_id"]):
                raise ProposalError("review/correction reused the worker session")
            # A late parent/source change invalidates the entire candidate.
            if head != git(root, "rev-parse", "HEAD") or initial_status != git(root, "status", "--porcelain=v1", "--untracked-files=all"):
                raise ProposalError("Git state changed during generation")
            current_task, current_sources = load_task(root, task_path)
            if current_task != task or current_sources != sources:
                raise ProposalError("task or source bytes changed during generation")
            write_json(out / "result.json", result)
            (out / "proposal.patch").write_text(proposal_diff(result["changes"], sources))
            meta.update(state=result["status"], conversation_id=envelope["conversation_id"],
                        duration_seconds=round(time.time() - meta["started_at"], 3),
                        usage=envelope.get("usage", {}),
                        result_sha256=file_sha256(out / "result.json"))
        except BaseException as exc:
            meta.update(state="BLOCKED", error=type(exc).__name__ + ": " + str(exc),
                        duration_seconds=round(time.time() - meta["started_at"], 3))
            write_json(out / "status.json", meta)
            raise
        write_json(out / "status.json", meta)
        return out
    finally:
        release_proposal_lock(lock_descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("worker", "verifier"))
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--feedback", type=Path)
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--cli", type=Path, help="existing Antigravity executable (controller-owned)")
    args = parser.parse_args()
    try:
        out = run(args.root, args.task, args.role, args.run_id, args.timeout, args.feedback, args.proposal, args.cli)
        meta = unique_json(bounded_file_text(
            args.root.resolve(strict=True), out / "status.json", MAX_CONTEXT, "run status"
        ))
        print(json.dumps({"state": meta["state"], "evidence": str(out),
                          "seconds": meta["duration_seconds"]}))
        return 0 if meta["state"] in {"PROPOSAL_READY", "VERIFIED"} else 1
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"state": "BLOCKED", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
