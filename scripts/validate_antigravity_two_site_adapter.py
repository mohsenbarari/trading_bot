#!/usr/bin/env python3
"""Statically validate the repository-local Antigravity two-site adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from two_site_antigravity_session import binding, load_profile, UUID, MODEL
VALIDATED_CLI_SHA256 = "a0a6a8044d01accd39e6f5926d29648d212a2e519ff14102f09e1c061e6171dd"
AGENT_DIR = ROOT / ".agents" / "agents"
SKILL = ROOT / ".agents" / "skills" / "two-site-refactor" / "SKILL.md"
RESOURCE = SKILL.parent / "resources" / "AUTONOMOUS_ORCHESTRATION.md"
STATE_SCHEMA = SKILL.parent / "resources" / "antigravity-state.schema.json"
FENCE_SCHEMA = SKILL.parent / "resources" / "orchestrator-fence.schema.json"
RULE = ROOT / ".agents" / "rules" / "two-site-refactor-safety.md"
GUIDE = ROOT / "docs" / "refactor" / "two-server" / "ANTIGRAVITY_EXECUTION.md"
REVIEW_SCHEMA = ROOT / "docs" / "refactor" / "two-server" / "templates" / "final-review.schema.json"
CLOSEOUT_SCHEMA = ROOT / "docs" / "refactor" / "two-server" / "templates" / "closeout-review.schema.json"
CLOSEOUT_BRIDGE = ROOT / "scripts" / "two_site_codex_closeout_review.sh"
GOVERNANCE_SCHEMA = ROOT / "docs" / "refactor" / "two-server" / "templates" / "governance-review.schema.json"
GOVERNANCE_BRIDGE = ROOT / "scripts" / "two_site_codex_governance_review.sh"
FENCE_BRIDGE = ROOT / "scripts" / "two_site_orchestrator_fence.py"

OFFICIAL_TOOLS = {
    "view_file",
    "write_to_file",
    "replace_file_content",
    "list_dir",
    "find_by_name",
    "grep_search",
    "run_command",
    "manage_task",
    "schedule",
    "ask_permission",
    "invoke_subagent",
    "define_subagent",
    "send_message",
    "manage_subagents",
    "ask_question",
    "search_web",
    "read_url_content",
    "generate_image",
}

AGENTS: dict[str, dict[str, Any]] = {
    "two-site-coordinator/agent.md": {
        "name": "two-site-coordinator",
        "mainAgent": True,
        "subagent": False,
        "required_tools": {"invoke_subagent", "send_message", "manage_subagents"},
    },
    "two-site-stage-worker/agent.md": {
        "name": "two-site-stage-worker",
        "mainAgent": False,
        "subagent": True,
        "required_tools": {"write_to_file", "replace_file_content", "run_command"},
    },
    "two-site-stage-verifier/agent.md": {
        "name": "two-site-stage-verifier",
        "mainAgent": False,
        "subagent": True,
        "required_tools": {"view_file", "grep_search", "run_command", "write_to_file"},
    },
}

READ_TOOLS = {"view_file", "list_dir", "find_by_name", "grep_search", "run_command", "manage_task", "send_message"}
ALLOWED_ROLE_TOOLS = {
    "two-site-stage-verifier": READ_TOOLS | {"write_to_file"},
    "two-site-stage-worker": READ_TOOLS | {"write_to_file", "replace_file_content"},
    "two-site-coordinator": READ_TOOLS | {"write_to_file", "replace_file_content", "invoke_subagent", "manage_subagents"},
}

WRITE_TOOLS = {"write_to_file", "replace_file_content", "multi_replace_file_content"}
SMOKE_ROLES = ("coordinator", "worker", "verifier")


def _scalar(value: str) -> Any:
    value = value.strip()
    if value == "true":
        return True
    if value == "false":
        return False
    return value.strip("'\"")


def parse_frontmatter(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing opening frontmatter delimiter")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("missing closing frontmatter delimiter") from exc

    result: dict[str, Any] = {}
    current_list: str | None = None
    for raw in lines[1:end]:
        if raw.startswith("  - ") and current_list:
            result[current_list].append(_scalar(raw[4:]))
            continue
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" not in raw:
            raise ValueError(f"unsupported frontmatter line: {raw!r}")
        key, value = raw.split(":", 1)
        key = key.strip()
        if value.strip():
            result[key] = _scalar(value)
            current_list = None
        else:
            result[key] = []
            current_list = key
    return result


def validate(root: Path = ROOT) -> list[str]:
    root = root.resolve()
    agent_dir = root / ".agents" / "agents"
    skill = root / ".agents" / "skills" / "two-site-refactor" / "SKILL.md"
    resource = skill.parent / "resources" / "AUTONOMOUS_ORCHESTRATION.md"
    state_schema = skill.parent / "resources" / "antigravity-state.schema.json"
    fence_schema = skill.parent / "resources" / "orchestrator-fence.schema.json"
    rule = root / ".agents" / "rules" / "two-site-refactor-safety.md"
    guide = root / "docs" / "refactor" / "two-server" / "ANTIGRAVITY_EXECUTION.md"
    review_schema = root / "docs" / "refactor" / "two-server" / "templates" / "final-review.schema.json"
    closeout_schema = root / "docs" / "refactor" / "two-server" / "templates" / "closeout-review.schema.json"
    closeout_bridge = root / "scripts" / "two_site_codex_closeout_review.sh"
    governance_schema = root / "docs" / "refactor" / "two-server" / "templates" / "governance-review.schema.json"
    governance_bridge = root / "scripts" / "two_site_codex_governance_review.sh"
    fence_bridge = root / "scripts" / "two_site_orchestrator_fence.py"
    runtime_profile = root / "scripts" / "antigravity_cli_1_1_26_profile.json"
    errors: list[str] = []
    required = [
        skill,
        resource,
        state_schema,
        fence_schema,
        rule,
        guide,
        review_schema,
        closeout_schema,
        closeout_bridge,
        governance_schema,
        governance_bridge,
        fence_bridge,
        runtime_profile,
    ]
    required.extend(agent_dir / name for name in AGENTS)
    for path in required:
        if not path.is_file():
            errors.append(f"missing required file: {path.relative_to(root)}")

    if errors:
        return errors

    try:
        load_profile(runtime_profile)
    except (OSError, ValueError) as exc:
        errors.append(f"{runtime_profile.relative_to(root)}: {exc}")

    for filename, expected in AGENTS.items():
        path = agent_dir / filename
        try:
            meta = parse_frontmatter(path)
        except ValueError as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")
            continue

        for key in ("name", "description", "tools", "mainAgent", "subagent", "model", "commandExecutionPolicy"):
            if key not in meta:
                errors.append(f"{path.relative_to(root)}: missing frontmatter key {key}")
        for key in ("name", "mainAgent", "subagent"):
            if meta.get(key) != expected[key]:
                errors.append(f"{path.relative_to(root)}: {key} must be {expected[key]!r}")
        if meta.get("model") != "inherit":
            errors.append(f"{path.relative_to(root)}: model must inherit the explicitly pinned parent model")
        if meta.get("commandExecutionPolicy") != "sandbox":
            errors.append(f"{path.relative_to(root)}: commandExecutionPolicy must be sandbox")
        # CLI 1.1.26 lacks AgentBasePath for custom primary-agent skill references.
        # Load the canonical skill explicitly instead of declaring broken metadata.
        if "skills" in meta:
            errors.append(f"{path.relative_to(root)}: use explicit Skill reading, not skills metadata")
        body = path.read_text(encoding="utf-8")
        if ".agents/skills/two-site-refactor/SKILL.md" not in body or "stop with BLOCKED" not in body:
            errors.append(f"{path.relative_to(root)}: must explicitly read the canonical Skill and fail closed")

        tools = set(meta.get("tools", []))
        if tools != ALLOWED_ROLE_TOOLS[expected["name"]] or len(meta.get("tools", [])) != len(tools):
            errors.append(f"{path.relative_to(root)}: tools must match the exact reviewed role allowlist")
        unknown = tools - OFFICIAL_TOOLS
        if unknown:
            errors.append(f"{path.relative_to(root)}: unknown Antigravity tools {sorted(unknown)}")
        missing = expected["required_tools"] - tools
        if missing:
            errors.append(f"{path.relative_to(root)}: missing required tools {sorted(missing)}")
        if filename == "two-site-stage-verifier/agent.md" and tools & (WRITE_TOOLS - {"write_to_file"}):
            errors.append(f"{path.relative_to(root)}: Verifier exposes a source-edit tool")

    try:
        skill_meta = parse_frontmatter(skill)
        if skill_meta.get("name") != "two-site-refactor" or not skill_meta.get("description"):
            errors.append("Antigravity skill requires name and description")
    except ValueError as exc:
        errors.append(f"{skill.relative_to(root)}: {exc}")

    if len(rule.read_text(encoding="utf-8")) > 12_000:
        errors.append("workspace rule exceeds Antigravity's 12,000-character limit")

    for schema in (state_schema, fence_schema, review_schema, closeout_schema, governance_schema):
        try:
            document = json.loads(schema.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{schema.relative_to(root)}: invalid JSON: {exc}")
            continue
        if document.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            errors.append(f"{schema.relative_to(root)}: unexpected JSON Schema draft")

    combined = "\n".join(path.read_text(encoding="utf-8") for path in required)
    for required_text in (
        "CODEX_FINAL_REVIEWER",
        "scripts/two_site_codex_final_review.sh",
        "scripts/two_site_codex_closeout_review.sh",
        "scripts/two_site_codex_governance_review.sh",
        "scripts/two_site_orchestrator_fence.py",
        "refactor/two-site-architecture",
        "Workspace: inherit",
        "--dangerously-skip-permissions",
        "closeout commit",
        "Continue automatically",
        "orchestrator-fence.json",
        "worker-check",
        "stage-advance",
        "stage-open-check",
        "stage-bind",
    ):
        if required_text not in combined:
            errors.append(f"adapter omits required contract text: {required_text}")

    deprecated = root / ".agents" / "workflows"
    if deprecated.exists():
        errors.append("deprecated .agents/workflows must not be used by this adapter")
    return errors


def runtime_sessions(agy: str) -> dict[str, str]:
    """Read observed smoke IDs, not an approval or substitute for live validation."""
    path = ROOT / ".local" / "two-site-refactor" / "antigravity-runtime-sessions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {
        "schema_version", "cli_sha256", "coordinator", "worker", "verifier",
        "execution_coordinator",
    } or data["schema_version"] != "1.0":
        raise ValueError("invalid runtime session registry")
    digest = hashlib.sha256()
    with Path(agy).open("rb") as binary:
        for chunk in iter(lambda: binary.read(1_048_576), b""):
            digest.update(chunk)
    if data["cli_sha256"] != VALIDATED_CLI_SHA256 or digest.hexdigest() != VALIDATED_CLI_SHA256:
        raise ValueError("CLI binary changed; repeat version-specific runtime binding validation")
    ids = [data[key] for key in (*SMOKE_ROLES, "execution_coordinator")]
    if any(not isinstance(value, str) or not UUID.fullmatch(value) for value in ids) or len(set(ids)) != 4:
        raise ValueError("runtime requires four distinct smoke/execution conversation IDs")
    return data


def execution_binding(sessions: dict[str, str], conversation_id: str) -> dict[str, Any]:
    if not isinstance(conversation_id, str) or not UUID.fullmatch(conversation_id):
        raise ValueError("invalid execution Coordinator conversation ID")
    if sessions.get("execution_coordinator") != conversation_id:
        raise ValueError("execution Coordinator is not the registered conversation")
    if conversation_id in {sessions.get(role) for role in SMOKE_ROLES}:
        raise ValueError("execution Coordinator must be distinct from dedicated smoke sessions")
    metadata = AGENTS["two-site-coordinator/agent.md"]
    return binding(
        conversation_id,
        metadata["name"],
        ROOT,
        set(parse_frontmatter(AGENT_DIR / "two-site-coordinator" / "agent.md")["tools"]),
        history_policy="ACTIVE_EXECUTION",
    )


def runtime_bindings(
    sessions: dict[str, str],
    execution_conversation: str | None = None,
) -> list[dict[str, Any]]:
    result = []
    for filename, metadata in AGENTS.items():
        role = "coordinator" if metadata["mainAgent"] else (
            "worker" if metadata["name"].endswith("worker") else "verifier"
        )
        result.append(binding(
            sessions[role], metadata["name"], ROOT,
            set(parse_frontmatter(AGENT_DIR / filename)["tools"]),
            parent_id=None if role == "coordinator" else sessions["coordinator"],
        ))
    if execution_conversation is not None:
        result.append(execution_binding(sessions, execution_conversation))
    return result


def antigravity_cli() -> str | None:
    configured = os.environ.get("ANTIGRAVITY_CLI")
    local = ROOT / ".local" / "tools" / "agy"
    return configured or shutil.which("agy") or (str(local) if local.is_file() else None)


def validate_execution_conversation(conversation_id: str) -> list[str]:
    """Bind the actual Coordinator without resuming it or making a provider call."""
    agy = antigravity_cli()
    if not agy:
        return ["agy CLI is not installed; set ANTIGRAVITY_CLI or install .local/tools/agy"]
    try:
        sessions = runtime_sessions(agy)
        execution_binding(sessions, conversation_id)
    except (OSError, ValueError, sqlite3.Error) as exc:
        return [f"Antigravity execution Coordinator binding is not ready: {exc}"]
    return []


def check_smoke_stream(output: str, conversation_id: str, token: str = "ANTIGRAVITY_RUNTIME_SMOKE_OK") -> list[str]:
    try:
        events = [json.loads(line) for line in output.splitlines() if line.strip()]
        if any(not isinstance(item, dict) for item in events):
            raise ValueError("event must be an object")
        initial = [item for item in events if item.get("event") == "init"]
        results = [item for item in events if item.get("event") == "result"]
        if len(initial) != 1 or len(results) != 1 or events[0] != initial[0] or events[-1] != results[0]:
            raise ValueError("expected one ordered init/result pair")
        init = initial[0]
        result = results[0]["result"]
        if (init["conversation_id"] != conversation_id
                or init["init"]["cwd"] != str(ROOT)
                or init["init"]["permission_mode"] != "request-review"
                or init["init"].get("model") != MODEL
                or result["conversation_id"] != conversation_id
                or result["status"] != "SUCCESS"
                or result.get("error")
                or result["response"].strip() != token
                or type(result["num_turns"]) is not int or result["num_turns"] < 1):
            raise ValueError("exact response/session/mode contract failed")
        responses = []
        for event in events[1:-1]:
            step = event.get("step_update", {})
            if (event.get("event") != "step_update"
                    or step.get("conversation_id") != conversation_id
                    or step.get("step_type") not in {"user_input", "system_message", "agent_response"}
                    or any(step.get(key) for key in ("tool_name", "tool_info", "subagent_info"))):
                raise ValueError("unexpected tool, subagent, or foreign-session step")
            if step.get("step_type") == "agent_response":
                responses.append(step)
        if not responses or not any(item.get("state") == "DONE" for item in responses):
            raise ValueError("missing completed response in current turn")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return [f"agy read-only smoke failed: {exc}"]
    return []


def validate_runtime(execution_conversation: str | None = None) -> list[str]:
    agy = antigravity_cli()
    if not agy:
        return ["agy CLI is not installed; set ANTIGRAVITY_CLI or install .local/tools/agy"]

    try:
        sessions = runtime_sessions(agy)
        execution_conversation = execution_conversation or sessions.get("execution_coordinator")
        if not isinstance(execution_conversation, str):
            raise ValueError("runtime registry has no execution Coordinator")
        bindings_before = runtime_bindings(sessions, execution_conversation)
    except (OSError, ValueError, sqlite3.Error) as exc:
        return [f"Antigravity runtime binding is not ready: {exc}"]

    def repository_snapshot() -> tuple[str, str]:
        head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return head, status

    def control_state_snapshot() -> str:
        base = ROOT / ".local" / "two-site-refactor"
        digest = hashlib.sha256()
        if not base.exists():
            return digest.hexdigest()
        for path in sorted(item for item in base.rglob("*") if item.is_file()):
            relative = path.relative_to(base).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            content = path.read_bytes()
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        return digest.hexdigest()

    try:
        before = repository_snapshot(), control_state_snapshot()
    except (OSError, subprocess.CalledProcessError) as exc:
        return [f"could not capture pre-smoke repository identity: {exc}"]

    def finish(errors: list[str]) -> list[str]:
        try:
            after = repository_snapshot(), control_state_snapshot()
            bindings_after = runtime_bindings(sessions, execution_conversation)
        except (OSError, ValueError, sqlite3.Error, subprocess.CalledProcessError) as exc:
            return [*errors, f"could not capture post-smoke repository identity: {exc}"]
        if after != before or bindings_after != bindings_before:
            return [
                *errors,
                "Antigravity runtime validation changed repository HEAD, full status, or local orchestration state",
            ]
        return errors

    tokens = {"coordinator": "ANTIGRAVITY_RUNTIME_SMOKE_OK",
              "worker": "WORKER_SMOKE_OK", "verifier": "VERIFIER_SMOKE_OK"}
    for role, token in tokens.items():
        try:
            smoke = subprocess.run(
                [agy, "--conversation", sessions[role], "--model", MODEL,
                 "--effort", "high", "--sandbox", "--mode", "plan",
                 "--output-format", "stream-json", "--print-timeout", "2m", "-p",
                 "Non-Stage runtime validation only. Do not invoke tools or read/write files. "
                 f"Reply exactly {token}"],
                cwd=ROOT, check=False, capture_output=True, text=True, timeout=150,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return finish([f"agy read-only runtime smoke could not complete: {exc}"])
        if smoke.returncode != 0:
            normalized = f"{smoke.stderr}\n{smoke.stdout}".lower()
            if any(marker in normalized for marker in (
                "authentication required", "authentication failed", "not logged into antigravity",
                "you are not logged in", "sign in",
            )):
                return finish([f"agy read-only {role} smoke failed: authentication required; complete one interactive agy login"])
            return finish([f"agy read-only {role} smoke failed: runtime command exited unsuccessfully"])
        errors = finish(check_smoke_stream(smoke.stdout, sessions[role], token))
        if errors:
            return errors
    return finish([])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", action="store_true", help="also verify exact bound sessions and live no-tool smoke for all three roles")
    parser.add_argument(
        "--execution-conversation",
        metavar="UUID",
        help="bind the registered actual Antigravity Coordinator without resuming it",
    )
    args = parser.parse_args()

    errors = validate()
    if args.runtime and not errors:
        errors.extend(validate_runtime(args.execution_conversation))
    elif args.execution_conversation and not errors:
        errors.extend(validate_execution_conversation(args.execution_conversation))
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: Antigravity two-site adapter structure and safety contracts are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
