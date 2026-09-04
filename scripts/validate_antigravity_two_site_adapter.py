#!/usr/bin/env python3
"""Statically validate the repository-local Antigravity two-site adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
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
    "multi_replace_file_content",
    "list_dir",
    "find_by_name",
    "grep_search",
    "run_command",
    "manage_task",
    "schedule",
    "list_permissions",
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
    "two-site-coordinator.md": {
        "name": "two-site-coordinator",
        "mainAgent": True,
        "subagent": False,
        "required_tools": {"invoke_subagent", "send_message", "manage_subagents"},
    },
    "two-site-stage-worker.md": {
        "name": "two-site-stage-worker",
        "mainAgent": False,
        "subagent": True,
        "required_tools": {"write_to_file", "replace_file_content", "run_command"},
    },
    "two-site-stage-verifier.md": {
        "name": "two-site-stage-verifier",
        "mainAgent": False,
        "subagent": True,
        "required_tools": {"view_file", "grep_search", "run_command"},
    },
}

WRITE_TOOLS = {"write_to_file", "replace_file_content", "multi_replace_file_content"}


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
    ]
    required.extend(agent_dir / name for name in AGENTS)
    for path in required:
        if not path.is_file():
            errors.append(f"missing required file: {path.relative_to(root)}")

    if errors:
        return errors

    for filename, expected in AGENTS.items():
        path = agent_dir / filename
        try:
            meta = parse_frontmatter(path)
        except ValueError as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")
            continue

        for key in ("name", "description", "tools", "mainAgent", "subagent", "model", "commandExecutionPolicy", "skills"):
            if key not in meta:
                errors.append(f"{path.relative_to(root)}: missing frontmatter key {key}")
        for key in ("name", "mainAgent", "subagent"):
            if meta.get(key) != expected[key]:
                errors.append(f"{path.relative_to(root)}: {key} must be {expected[key]!r}")
        if meta.get("model") != "pro":
            errors.append(f"{path.relative_to(root)}: model must be pro")
        if meta.get("commandExecutionPolicy") != "sandbox":
            errors.append(f"{path.relative_to(root)}: commandExecutionPolicy must be sandbox")
        if meta.get("skills") != ["skills/two-site-refactor"]:
            errors.append(f"{path.relative_to(root)}: must load skills/two-site-refactor")

        tools = set(meta.get("tools", []))
        unknown = tools - OFFICIAL_TOOLS
        if unknown:
            errors.append(f"{path.relative_to(root)}: unknown Antigravity tools {sorted(unknown)}")
        missing = expected["required_tools"] - tools
        if missing:
            errors.append(f"{path.relative_to(root)}: missing required tools {sorted(missing)}")
        if filename == "two-site-stage-verifier.md" and tools & WRITE_TOOLS:
            errors.append(f"{path.relative_to(root)}: Verifier exposes write tools")

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
        "stage-bind",
    ):
        if required_text not in combined:
            errors.append(f"adapter omits required contract text: {required_text}")

    deprecated = root / ".agents" / "workflows"
    if deprecated.exists():
        errors.append("deprecated .agents/workflows must not be used by this adapter")
    return errors


def validate_runtime() -> list[str]:
    configured = os.environ.get("ANTIGRAVITY_CLI")
    local = ROOT / ".local" / "tools" / "agy"
    agy = configured or shutil.which("agy") or (str(local) if local.is_file() else None)
    if not agy:
        return ["agy CLI is not installed; set ANTIGRAVITY_CLI or install .local/tools/agy"]

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
        except (OSError, subprocess.CalledProcessError) as exc:
            return [*errors, f"could not capture post-smoke repository identity: {exc}"]
        if after != before:
            return [
                *errors,
                "Antigravity runtime validation changed repository HEAD, full status, or local orchestration state",
            ]
        return errors

    try:
        result = subprocess.run(
            [agy, "--output-format", "json", "agents"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return finish([f"agy agents could not complete: {exc}"])
    if result.returncode != 0:
        return finish([f"agy agents failed with exit {result.returncode}: {result.stderr.strip()}"])
    try:
        agents_result = json.loads(result.stdout)
        discovered = agents_result["command"]["data"]["agents"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return finish([f"agy agents returned an invalid JSON envelope: {exc}"])
    names: set[str] = set()
    if isinstance(discovered, list):
        for item in discovered:
            if isinstance(item, str):
                names.add(item)
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                names.add(item["name"])
    missing = [name[0:-3] for name in AGENTS if name[0:-3] not in names]
    if missing:
        return finish([f"agy did not discover custom agent: {name}" for name in missing])
    smoke_token = "ANTIGRAVITY_RUNTIME_SMOKE_OK"
    try:
        smoke = subprocess.run(
            [
                agy,
                "--agent",
                "two-site-coordinator",
                "--sandbox",
                "--mode",
                "plan",
                "--output-format",
                "json",
                "--print-timeout",
                "2m",
                "-p",
                f"Runtime validation only. Do not invoke tools or modify anything. Reply exactly {smoke_token}",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=150,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return finish([f"agy read-only runtime smoke could not complete: {exc}"])
    if smoke.returncode != 0:
        combined = f"{smoke.stderr}\n{smoke.stdout}"
        detail = (
            "authentication required; complete one interactive agy login"
            if "Authentication required" in combined or "authentication failed" in combined
            else "runtime command exited unsuccessfully"
        )
        return finish([f"agy read-only coordinator smoke failed: {detail}"])
    try:
        smoke_result = json.loads(smoke.stdout)
    except json.JSONDecodeError as exc:
        return finish([f"agy read-only coordinator smoke returned invalid JSON: {exc}"])
    if (
        smoke_result.get("status") != "SUCCESS"
        or str(smoke_result.get("response", "")).strip() != smoke_token
        or not smoke_result.get("conversation_id")
        or int(smoke_result.get("num_turns", 0)) < 1
    ):
        return finish(["agy read-only coordinator smoke did not return the exact success contract"])
    return finish([])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", action="store_true", help="also require agy CLI discovery")
    args = parser.parse_args()

    errors = validate()
    if args.runtime:
        errors.extend(validate_runtime())
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: Antigravity two-site adapter structure and safety contracts are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
