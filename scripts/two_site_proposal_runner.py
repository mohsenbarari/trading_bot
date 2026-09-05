#!/usr/bin/env python3
"""Bounded, tool-free Antigravity proposals. Only Codex applies/reviews/commits them.

The controller writes ignored run artifacts only; it does not apply generated code,
change Git refs/Stage state or deploy. The CLI uses plan/sandbox mode and a deny hook;
tool attempts invalidate its result. Each invocation is a fresh session.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
MODEL = "gemini-3.8-flash-high"
MAX_CONTEXT = 160_000
MAX_OUTPUT = 2_000_000
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


class ProposalError(ValueError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
    raw = task_path.read_bytes()
    if len(raw) > MAX_CONTEXT:
        raise ProposalError("task exceeds context budget")
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
        if not target.is_file() or target.stat().st_size > MAX_CONTEXT:
            raise ProposalError("context file is not a bounded regular file")
        content = target.read_bytes()
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
    if any(e.get("event") == "step_update" and (
            e["step_update"].get("tool_name") or e["step_update"].get("tool_info")
            or e["step_update"].get("subagent_info")
            or e["step_update"].get("step_type") in {"tool", "subagent"}) for e in events):
        raise ProposalError("tool attempt invalidates a proposal-only run")
    envelope = terminal[0]
    session = init[0].get("conversation_id")
    if (not isinstance(session, str) or not session.strip()
            or envelope.get("status") != "SUCCESS" or envelope.get("denied_actions")
            or envelope.get("conversation_id") != init[0].get("conversation_id")):
        raise ProposalError("CLI did not complete one successful tool-free session")
    # With --json-schema the documented machine payload is structured_output.
    # CLI 1.1.26 may also append its submission object to free-text response.
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
    task, sources = load_task(root, task_path)
    head = git(root, "rev-parse", "HEAD")
    initial_status = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    material = {"task": task, "source_head": head, "sources": sources}
    for key, path in (("feedback", feedback), ("proposal", proposal)):
        if path is not None:
            content = path.read_bytes()
            if len(content) > MAX_CONTEXT or SECRET.search(content.decode()):
                raise ProposalError("feedback/proposal exceeds the safe context budget")
            material[key] = content.decode()
    if role == "verifier" and proposal is None:
        raise ProposalError("verifier needs the exact proposal")
    if role == "verifier":
        baseline_path = proposal.with_name("task.json")
        if baseline_path.stat().st_size > 2 * MAX_CONTEXT:
            raise ProposalError("worker snapshot exceeds budget")
        baseline = unique_json(baseline_path.read_text())
        if (not isinstance(baseline, dict) or baseline.get("task") != task
                or baseline.get("source_head") != head or baseline.get("sources") != sources):
            raise ProposalError("verify before applying: worker baseline differs from current source/task")
        candidate = validate_result(unique_json(material["proposal"]), task, sources, "worker")
        if candidate["status"] != "PROPOSAL_READY":
            raise ProposalError("verifier requires a complete worker proposal")
    prompt = (ROOT / "docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md").read_text()
    prompt += "\nROLE: " + role + "\nCONTEXT_JSON:\n" + json.dumps(material, ensure_ascii=False)
    if len(prompt.encode()) > 2 * MAX_CONTEXT:
        raise ProposalError("split task or feedback into a smaller coherent unit")
    if SECRET.search(prompt):
        raise ProposalError("credential-like content excluded from model input")
    cli = (cli or ROOT / ".local/tools/agy").resolve(strict=True)
    local = root / ".local/two-site-refactor/proposals"
    if local.resolve() != local:
        raise ProposalError("run storage cannot be a symlink")
    local.mkdir(parents=True, exist_ok=True)
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
            "authority": "PROPOSAL_ONLY_NOT_ACCEPTANCE"}
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
            try:
                meta["cli_pid"] = process.pid
                write_json(out / "status.json", meta)
                raw, error = process.communicate(
                    (json.dumps({"event": "user", "message": {"content": prompt}}) + "\n").encode(),
                    timeout=timeout + 15,
                )
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
                raise
        if len(raw) + len(error) > MAX_OUTPUT:
            raise ProposalError("CLI output exceeds budget")
        (out / "stream.jsonl").write_bytes(raw)
        (out / "stderr.txt").write_bytes(error)
        if process.returncode:
            raise ProposalError(f"CLI failed with exit {process.returncode}; inspect run evidence")
        result, envelope = parse_stream(raw.decode(), task, sources, role)
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
                    usage=envelope.get("usage", {}), result_sha256=sha((out / "result.json").read_bytes()))
    except BaseException as exc:
        meta.update(state="BLOCKED", error=type(exc).__name__ + ": " + str(exc),
                    duration_seconds=round(time.time() - meta["started_at"], 3))
        write_json(out / "status.json", meta)
        raise
    write_json(out / "status.json", meta)
    return out


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
        meta = unique_json((out / "status.json").read_text())
        print(json.dumps({"state": meta["state"], "evidence": str(out),
                          "seconds": meta["duration_seconds"]}))
        return 0 if meta["state"] in {"PROPOSAL_READY", "VERIFIED"} else 1
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"state": "BLOCKED", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
