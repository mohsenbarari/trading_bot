#!/usr/bin/python3 -I
"""Atomically fence Cursor/Antigravity ownership of two-site repository writes."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

import yaml


ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local" / "two-site-refactor"
FENCE = LOCAL / "orchestrator-fence.json"
LOCK = LOCAL / "orchestrator-fence.lock"
REGISTRY = LOCAL / "worktrees.yaml"
EXECUTION_ORDER = ROOT / "docs" / "refactor" / "two-server" / "execution-order.yaml"
INTEGRATION_BRANCH = "refactor/two-site-architecture"
GIT_BINARY = "/usr/bin/git"
PLATFORMS = {"CURSOR", "ANTIGRAVITY"}
NON_RUNNING = {"IDLE", "IDLE_INTEGRATED", "BLOCKED", "READY", "COMPLETE_CANDIDATE"}
ACTIVE_STAGE_STATES = {"READY", "RUNNING", "IDLE_INTEGRATED", "COMPLETE_CANDIDATE", "BLOCKED"}
ASSIGNMENT_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]*"
PROTECTED_ASSIGNMENT_PATTERNS = {
    ".agents/**",
    "docs/refactor/two-server/CHANGE_LEDGER.md",
    "docs/refactor/two-server/execution-order.yaml",
    "docs/refactor/two-server/reviews/**",
    "scripts/two_site_agent_*.py",
}
ANTIGRAVITY_INSTANCE_PATTERN = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
IMMUTABLE_ASSIGNMENT_FIELDS = (
    "stage_id",
    "branch",
    "worktree_path",
    "worktree_registry_id",
    "allowed_paths",
    "forbidden_paths",
    "locks",
    "parallel_peer",
    "deliverables",
    "tests",
    "failure_tests",
    "rollback_test",
    "external_access",
    "expires_at",
)
CANONICAL_ASSIGNMENT_FIELDS = (
    "assignment_id",
    "stage_id",
    "role",
    "status",
    "orchestrator",
    "orchestration_epoch",
    "supersedes",
    "base_sha",
    "branch",
    "worktree_path",
    "worktree_registry_id",
    "allowed_paths",
    "forbidden_paths",
    "locks",
    "parallel_peer",
    "deliverables",
    "tests",
    "failure_tests",
    "rollback_test",
    "external_access",
    "expires_at",
    "issued_by",
    "issued_at",
    "integration_branch",
    "integration_head_at_issue",
)
STAGE_METADATA_FIELDS = {
    "state",
    "section",
    "depends_on",
    "production",
    "destructive",
    "dns_change",
    "writer_change",
    "secret_change",
}
EXTERNAL_STAGE_FLAGS = (
    "production",
    "destructive",
    "dns_change",
    "writer_change",
    "secret_change",
)
OPTIONAL_ASSIGNMENT_FIELDS = {"issued_from_worktree"}
ASSIGNMENT_LIST_FIELDS = (
    "allowed_paths",
    "forbidden_paths",
    "locks",
    "deliverables",
    "tests",
    "failure_tests",
)


class FenceError(RuntimeError):
    pass


def validate_coordinator_instance(platform: str, instance: Any) -> None:
    if platform not in PLATFORMS:
        raise FenceError("orchestrator platform is invalid")
    if not isinstance(instance, str) or not instance:
        raise FenceError("orchestrator instance ID is invalid")
    if platform == "ANTIGRAVITY" and not re.fullmatch(ANTIGRAVITY_INSTANCE_PATTERN, instance):
        raise FenceError("Antigravity instance must be the execution Coordinator conversation UUID")


def run_git(*args: str, cwd: Path | None = None) -> str:
    cwd = ROOT if cwd is None else cwd
    result = subprocess.run(
        [GIT_BINARY, "-C", str(cwd), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise FenceError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FenceError(f"required YAML is missing: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise FenceError(f"YAML root must be an object: {path}")
    return payload


def load_assignment(assignment_id: str) -> dict[str, Any]:
    path = LOCAL / "assignments" / f"{assignment_id}.md"
    if not path.is_file():
        raise FenceError(f"replacement Assignment is missing: {path}")
    match = re.search(r"```yaml\n(.*?)\n```", path.read_text(encoding="utf-8"), re.S)
    if not match:
        raise FenceError("replacement Assignment has no YAML contract")
    payload = yaml.safe_load(match.group(1))
    if not isinstance(payload, dict):
        raise FenceError("replacement Assignment YAML must be an object")
    return payload


def load_fence(required: bool = True) -> dict[str, Any] | None:
    if not FENCE.is_file():
        if required:
            raise FenceError("orchestrator fence is missing")
        return None
    try:
        payload = json.loads(FENCE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FenceError(f"orchestrator fence is invalid JSON: {exc}") from exc
    validate_fence(payload)
    return payload


def load_bounded_unique_json(path: Path, limit: int) -> dict[str, Any]:
    """Read one regular JSON file without following a final symlink or duplicate keys."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise FenceError(f"cannot open bounded JSON input: {path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise FenceError("bounded JSON input is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            value = source.read(limit + 1)
        if len(value) > limit:
            raise FenceError("bounded JSON input exceeds its size limit")
    finally:
        os.close(descriptor)

    def unique_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise FenceError("bounded JSON input has duplicate keys")
            result[key] = item
        return result

    def reject_constant(_value: str) -> None:
        raise FenceError("bounded JSON input contains a non-finite number")

    try:
        payload = json.loads(
            value,
            object_pairs_hook=unique_pairs,
            parse_constant=reject_constant,
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise FenceError("bounded JSON input is invalid") from exc
    if not isinstance(payload, dict):
        raise FenceError("bounded JSON input root must be an object")
    return payload


def canonical_json_sha256(payload: Any) -> str:
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (ValueError, TypeError, RecursionError) as exc:
        raise FenceError("JSON value cannot be canonically hashed") from exc
    return hashlib.sha256(canonical).hexdigest()


def validate_fence(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise FenceError("orchestrator fence root must be an object")
    required = {
        "schema_version",
        "epoch",
        "status",
        "binding_kind",
        "platform",
        "coordinator_instance_id",
        "integration_branch",
        "integration_head_sha",
        "previous_assignment_id",
        "active_assignment_id",
        "assignment_base_sha",
        "assignment_integration_head_at_issue",
        "immutable_contract_sha256",
        "claimed_at_utc",
        "supersedes",
    }
    if set(payload) != required:
        raise FenceError("orchestrator fence keys do not match schema")
    if payload["schema_version"] != "1.0" or payload["platform"] not in PLATFORMS:
        raise FenceError("orchestrator fence schema/platform is invalid")
    if payload["status"] not in {"RECONCILING", "ACTIVE", "RELEASED", "BLOCKED"}:
        raise FenceError("orchestrator fence status is invalid")
    if payload["binding_kind"] not in {"TAKEOVER", "STAGE"}:
        raise FenceError("orchestrator fence binding kind is invalid")
    if not isinstance(payload["epoch"], int) or payload["epoch"] < 1:
        raise FenceError("orchestrator fence epoch is invalid")
    validate_coordinator_instance(payload["platform"], payload["coordinator_instance_id"])
    if payload["integration_branch"] != INTEGRATION_BRANCH:
        raise FenceError("orchestrator fence integration branch is invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", str(payload["integration_head_sha"])):
        raise FenceError("orchestrator fence integration SHA is invalid")
    for key in ("previous_assignment_id", "active_assignment_id"):
        if payload[key] is not None and (
            not isinstance(payload[key], str)
            or not re.fullmatch(ASSIGNMENT_ID_PATTERN, payload[key])
        ):
            raise FenceError(f"orchestrator fence {key} is invalid")
    if payload["previous_assignment_id"] is None:
        raise FenceError("orchestrator fence previous Assignment is required")
    if payload["status"] in {"ACTIVE", "RELEASED"} and payload["active_assignment_id"] is None:
        raise FenceError("active/released fence requires an active Assignment")
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload["immutable_contract_sha256"])):
        raise FenceError("orchestrator fence immutable contract digest is invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", str(payload["assignment_base_sha"])):
        raise FenceError("orchestrator fence Assignment base SHA is invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", str(payload["assignment_integration_head_at_issue"])):
        raise FenceError("orchestrator fence Assignment integration issue SHA is invalid")
    try:
        datetime.fromisoformat(str(payload["claimed_at_utc"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise FenceError("orchestrator fence timestamp is invalid") from exc
    supersedes = payload["supersedes"]
    if supersedes is not None:
        if not isinstance(supersedes, dict) or set(supersedes) != {
            "epoch",
            "platform",
            "coordinator_instance_id",
        }:
            raise FenceError("orchestrator fence supersedes value is invalid")
        if supersedes["platform"] not in PLATFORMS or not isinstance(supersedes["epoch"], int):
            raise FenceError("orchestrator fence superseded owner is invalid")
        validate_coordinator_instance(
            supersedes["platform"], supersedes["coordinator_instance_id"]
        )


@contextmanager
def locked() -> Iterator[None]:
    LOCAL.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_text(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_write_fence(payload: dict[str, Any]) -> None:
    validate_fence(payload)
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write_text(FENCE, content)


def atomic_write_yaml(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))


def immutable_contract_sha256(assignment: dict[str, Any]) -> str:
    missing = [key for key in IMMUTABLE_ASSIGNMENT_FIELDS if key not in assignment]
    if missing:
        raise FenceError(f"Assignment omits immutable fields: {', '.join(missing)}")
    contract = {key: assignment[key] for key in IMMUTABLE_ASSIGNMENT_FIELDS}
    canonical = json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def validate_assignment_structure(assignment: dict[str, Any]) -> None:
    if not re.fullmatch(r"[A-Z][A-Z0-9]*-[0-9]+[a-z]?", str(assignment.get("stage_id", ""))):
        raise FenceError("Assignment Stage ID is invalid")
    expected_branch = rf"refactor/stage/{re.escape(str(assignment['stage_id']))}(?:-[A-Za-z0-9][A-Za-z0-9._-]*)?"
    if not isinstance(assignment.get("branch"), str) or not re.fullmatch(
        expected_branch, assignment["branch"]
    ):
        raise FenceError("Assignment branch is invalid")
    if not isinstance(assignment.get("worktree_path"), str) or not Path(
        assignment["worktree_path"]
    ).is_absolute():
        raise FenceError("Assignment worktree path is invalid")
    if not isinstance(assignment.get("worktree_registry_id"), str) or not re.fullmatch(
        ASSIGNMENT_ID_PATTERN, assignment["worktree_registry_id"]
    ):
        raise FenceError("Assignment worktree registry ID is invalid")
    for key in ASSIGNMENT_LIST_FIELDS:
        value = assignment.get(key)
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item for item in value
        ):
            raise FenceError(f"Assignment {key} must be an array of non-empty strings")
    for key in ("supersedes", "parallel_peer"):
        value = assignment.get(key)
        if value is not None and (
            not isinstance(value, str) or not re.fullmatch(ASSIGNMENT_ID_PATTERN, value)
        ):
            raise FenceError(f"Assignment {key} is invalid")
    rollback = assignment.get("rollback_test")
    if rollback is not None and (not isinstance(rollback, str) or not rollback):
        raise FenceError("Assignment rollback test is invalid")
    if assignment.get("external_access") != "NONE":
        raise FenceError("automatic Assignment external access must be NONE")


def validate_canonical_assignment(assignment: dict[str, Any]) -> None:
    missing = [key for key in CANONICAL_ASSIGNMENT_FIELDS if key not in assignment]
    if missing:
        raise FenceError(f"Assignment omits canonical fields: {', '.join(missing)}")
    unknown = sorted(set(assignment) - set(CANONICAL_ASSIGNMENT_FIELDS) - OPTIONAL_ASSIGNMENT_FIELDS)
    if unknown:
        raise FenceError(f"Assignment has unknown fields: {', '.join(unknown)}")
    validate_assignment_structure(assignment)
    if not re.fullmatch(ASSIGNMENT_ID_PATTERN, str(assignment["assignment_id"])):
        raise FenceError("Assignment ID is invalid")
    if assignment["role"] != "WORKER" or assignment["issued_by"] != "EXECUTION_COORDINATOR":
        raise FenceError("Assignment authority is invalid")
    if assignment["status"] not in {"READY", "IN_PROGRESS"}:
        raise FenceError("Assignment status is invalid")
    if assignment["orchestrator"] not in PLATFORMS:
        raise FenceError("Assignment orchestrator is invalid")
    if not isinstance(assignment["orchestration_epoch"], int) or assignment["orchestration_epoch"] < 1:
        raise FenceError("Assignment orchestration epoch is invalid")
    if assignment["integration_branch"] != INTEGRATION_BRANCH:
        raise FenceError("Assignment integration branch is invalid")
    for key in ("base_sha", "integration_head_at_issue"):
        if not re.fullmatch(r"[0-9a-f]{40}", str(assignment[key])):
            raise FenceError(f"Assignment {key} is invalid")
    if "issued_from_worktree" in assignment and (
        not isinstance(assignment["issued_from_worktree"], str)
        or not Path(assignment["issued_from_worktree"]).is_absolute()
    ):
        raise FenceError("Assignment issuing worktree is invalid")
    try:
        issued_at = datetime.fromisoformat(str(assignment["issued_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise FenceError("Assignment issue timestamp is invalid") from exc
    if issued_at.tzinfo is None:
        raise FenceError("Assignment issue timestamp must include a timezone")
    require_unexpired(assignment)
    immutable_contract_sha256(assignment)


def git_common_dir(worktree: Path) -> Path:
    raw = Path(run_git("rev-parse", "--git-common-dir", cwd=worktree))
    if not raw.is_absolute():
        raw = worktree / raw
    return raw.resolve()


def require_integration_repository(worktree: Path) -> None:
    if git_common_dir(worktree) != git_common_dir(ROOT):
        raise FenceError("registered Stage worktree belongs to a different Git repository")


def validate_automatic_stage(
    stages: dict[str, Any],
    current_stage_id: str,
    next_stage_id: str,
    *,
    expected_state: str,
) -> None:
    current_state = stages.get(current_stage_id)
    next_state = stages.get(next_stage_id)
    if not isinstance(current_state, dict) or not isinstance(next_state, dict):
        raise FenceError("current or next Stage is missing from canonical execution order")
    if current_state.get("state") != "COMPLETE":
        raise FenceError("current Stage is not COMPLETE in canonical execution order")
    if next_state.get("state") != expected_state:
        raise FenceError(f"next Stage is not {expected_state} in canonical execution order")
    unknown = sorted(set(next_state) - STAGE_METADATA_FIELDS)
    if unknown:
        raise FenceError(f"next Stage has unknown fail-closed metadata: {', '.join(unknown)}")
    for flag in EXTERNAL_STAGE_FLAGS:
        value = next_state.get(flag, False)
        if not isinstance(value, bool):
            raise FenceError(f"next Stage {flag} flag is not boolean")
        if value:
            raise FenceError(f"automatic Stage continuation forbids {flag}")
    dependencies = next_state.get("depends_on")
    if not isinstance(dependencies, list) or any(
        not isinstance(dependency, str)
        or not isinstance(stages.get(dependency), dict)
        or stages[dependency].get("state") != "COMPLETE"
        for dependency in dependencies
    ):
        raise FenceError("next Stage dependencies are not all COMPLETE")


def require_unexpired(assignment: dict[str, Any]) -> None:
    raw = assignment.get("expires_at")
    if not isinstance(raw, str):
        raise FenceError("replacement Assignment expiry is missing")
    try:
        expires_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FenceError("replacement Assignment expiry is invalid") from exc
    if expires_at.tzinfo is None:
        raise FenceError("replacement Assignment expiry must include a timezone")
    if expires_at <= datetime.now(timezone.utc):
        raise FenceError("replacement Assignment is expired")


def assignment_document(source: str, assignment_id: str, changes: dict[str, Any], note: str) -> str:
    source = source.split("\n## Orchestrator handover\n", 1)[0].rstrip() + "\n"
    match = re.search(r"```yaml\n(.*?)\n```", source, re.S)
    if not match:
        raise FenceError("source Assignment has no YAML contract")
    payload = yaml.safe_load(match.group(1))
    if not isinstance(payload, dict):
        raise FenceError("source Assignment YAML must be an object")
    payload.update(changes)
    rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).rstrip()
    document = source[: match.start()] + f"```yaml\n{rendered}\n```" + source[match.end() :]
    document = re.sub(r"^# Work Assignment — `[^`]+`", f"# Work Assignment — `{assignment_id}`", document, count=1)
    return document.rstrip() + f"\n\n## Orchestrator handover\n\n{note}\n"


def verify_git(expected_head: str) -> None:
    if run_git("branch", "--show-current") != INTEGRATION_BRANCH:
        raise FenceError("integration branch mismatch")
    if run_git("rev-parse", "HEAD") != expected_head:
        raise FenceError("integration HEAD mismatch")


def verify_old_worker_idle(
    registry: dict[str, Any], assignment_id: str, expected_head: str
) -> dict[str, Any]:
    integration = registry.get("integration") or {}
    if integration.get("head_sha") != expected_head:
        raise FenceError("registry integration head is stale")
    if integration.get("active_assignment_id") != assignment_id:
        raise FenceError("registry active Assignment mismatch")
    matches = [
        item
        for item in registry.get("stage_worktrees", [])
        if item.get("assignment_id") == assignment_id
    ]
    if len(matches) != 1:
        raise FenceError("registry must contain exactly one active Assignment worktree")
    item = matches[0]
    if item.get("status") not in NON_RUNNING or item.get("dirty_uncommitted_candidate") is not False:
        raise FenceError("prior Worker is running, unknown, or dirty")
    worktree = Path(str(item.get("path", "")))
    if not worktree.is_dir():
        raise FenceError("registered Stage worktree is inaccessible")
    require_integration_repository(worktree)
    if run_git("status", "--porcelain=v1", "--untracked-files=all", cwd=worktree):
        raise FenceError("registered Stage worktree is not clean")
    if run_git("rev-parse", "HEAD", cwd=worktree) != item.get("head_sha"):
        raise FenceError("registered Stage worktree HEAD mismatch")
    return item


def validate_prior_takeover_contract(
    assignment: dict[str, Any],
    assignment_id: str,
    stage: dict[str, Any],
    *,
    allow_superseded: bool = False,
) -> None:
    validate_assignment_structure(assignment)
    allowed_statuses = {"READY", "IN_PROGRESS"}
    if allow_superseded:
        allowed_statuses.add("SUPERSEDED")
    identity_invalid = any(
        (
            assignment.get("assignment_id") != assignment_id,
            assignment.get("role") != "WORKER",
            assignment.get("status") not in allowed_statuses,
            assignment.get("issued_by") not in {"CURSOR_COORDINATOR", "EXECUTION_COORDINATOR"},
            assignment.get("stage_id") != stage.get("stage_id"),
            assignment.get("worktree_registry_id") != stage.get("registry_id"),
            assignment.get("branch") != stage.get("branch"),
            assignment.get("worktree_path") != stage.get("path"),
            assignment.get("locks") != stage.get("locks"),
        )
    )
    if identity_invalid:
        raise FenceError("prior Assignment does not match its registered worktree")
    if not re.fullmatch(r"[0-9a-f]{40}", str(assignment.get("base_sha", ""))):
        raise FenceError("prior Assignment base SHA is invalid")
    if stage.get("base_sha") != assignment.get("base_sha"):
        raise FenceError("prior Assignment base does not match registry")
    require_unexpired(assignment)
    worktree = Path(str(stage.get("path", "")))
    require_integration_repository(worktree)
    if run_git("branch", "--show-current", cwd=worktree) != stage.get("branch"):
        raise FenceError("prior Assignment branch does not match its worktree")
    actual_head = run_git("rev-parse", "HEAD", cwd=worktree)
    if actual_head != stage.get("head_sha"):
        raise FenceError("prior Assignment worktree HEAD does not match registry")
    if subprocess.run(
        [GIT_BINARY, "-C", str(worktree), "merge-base", "--is-ancestor", str(assignment["base_sha"]), actual_head],
        check=False,
        capture_output=True,
        text=True,
    ).returncode:
        raise FenceError("prior Assignment worktree does not descend from its base")
    immutable_contract_sha256(assignment)


def validate_stage_bind_integration(
    integration: dict[str, Any],
    args: argparse.Namespace,
    allowed_assignments: set[str],
) -> str:
    """Validate the complete integration owner while the canonical lock is held."""
    active_assignment = integration.get("active_assignment_id")
    if (
        integration.get("path") != str(ROOT)
        or integration.get("branch") != INTEGRATION_BRANCH
        or integration.get("head_sha") != args.integration_head
        or integration.get("owner_role") != "EXECUTION_COORDINATOR"
        or integration.get("orchestrator") != args.platform
        or integration.get("orchestration_epoch") != args.epoch
        or active_assignment not in allowed_assignments
    ):
        raise FenceError("registry integration ownership is not exact for Stage bind")
    return str(active_assignment)


def validate_stage_bind_prior_contract(
    assignment: dict[str, Any],
    assignment_id: str,
    stage: dict[str, Any],
    args: argparse.Namespace,
    *,
    expected_status: str,
) -> None:
    """Validate the exact prior Stage owner before any Stage-bind write."""
    validate_canonical_assignment(assignment)
    if (
        assignment.get("assignment_id") != assignment_id
        or assignment.get("role") != "WORKER"
        or assignment.get("status") not in {"READY", "IN_PROGRESS"}
        or assignment.get("issued_by") != "EXECUTION_COORDINATOR"
        or assignment.get("orchestrator") != args.platform
        or assignment.get("orchestration_epoch") != args.epoch
        or assignment.get("integration_branch") != INTEGRATION_BRANCH
        or assignment.get("stage_id") != stage.get("stage_id")
        or assignment.get("worktree_registry_id") != stage.get("registry_id")
        or assignment.get("branch") != stage.get("branch")
        or assignment.get("worktree_path") != stage.get("path")
        or assignment.get("base_sha") != stage.get("base_sha")
        or assignment.get("locks") != stage.get("locks")
        or stage.get("assignment_id") != assignment_id
        or stage.get("owner_role") != "EXECUTION_WORKER"
        or stage.get("orchestrator") != args.platform
        or stage.get("orchestration_epoch") != args.epoch
        or stage.get("status") != expected_status
        or stage.get("dirty_uncommitted_candidate") is not False
    ):
        raise FenceError("prior Stage ownership is not exact for Stage bind")
    worktree = Path(str(stage.get("path", "")))
    if not worktree.is_dir():
        raise FenceError("prior Stage worktree is inaccessible during Stage bind")
    require_integration_repository(worktree)
    if run_git("branch", "--show-current", cwd=worktree) != stage.get("branch"):
        raise FenceError("prior Stage worktree branch differs during Stage bind")
    actual_head = run_git("rev-parse", "HEAD", cwd=worktree)
    if actual_head != stage.get("head_sha"):
        raise FenceError("prior Stage worktree HEAD differs during Stage bind")
    if run_git("status", "--porcelain=v1", "--untracked-files=all", cwd=worktree):
        raise FenceError("prior Stage worktree is dirty during Stage bind")
    if subprocess.run(
        [
            GIT_BINARY,
            "-C",
            str(worktree),
            "merge-base",
            "--is-ancestor",
            str(assignment["base_sha"]),
            actual_head,
        ],
        check=False,
        capture_output=True,
        text=True,
    ).returncode:
        raise FenceError("prior Stage worktree does not descend from its Assignment base")


def validate_stage_bind_request(
    args: argparse.Namespace,
    next_assignment: dict[str, Any],
) -> None:
    """Bind the canonical transition to the exact typed gateway request."""
    if not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        str(args.request_id),
    ):
        raise FenceError("Stage-bind request ID is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(args.request_sha256)):
        raise FenceError("Stage-bind request digest is invalid")
    request_path = LOCAL / "gateway" / "requests" / f"{args.request_id}.json"
    request = load_bounded_unique_json(request_path, 1_000_000)
    if canonical_json_sha256(request) != args.request_sha256:
        raise FenceError("Stage-bind typed request digest differs")
    expected_request_keys = {
        "id",
        "operation",
        "actor_id",
        "assignment",
        "epoch",
        "integration_head",
        "next_stage",
        "next_assignment",
        "next_stage_commit_receipt_sha256",
        "contract",
    }
    if (
        not isinstance(request, dict)
        or set(request) != expected_request_keys
        or request.get("id") != args.request_id
        or request.get("operation") != "next-stage-bind"
        or not re.fullmatch(ANTIGRAVITY_INSTANCE_PATTERN, str(request.get("actor_id", "")))
        or request.get("actor_id") != args.instance
        or not re.fullmatch(ASSIGNMENT_ID_PATTERN, str(request.get("assignment", "")))
        or request.get("assignment") != args.assignment
        or not re.fullmatch(
            ASSIGNMENT_ID_PATTERN, str(request.get("next_assignment", ""))
        )
        or request.get("next_assignment") != args.next_assignment
        or isinstance(request.get("epoch"), bool)
        or not isinstance(request.get("epoch"), int)
        or request.get("epoch") != args.epoch
        or not re.fullmatch(r"[0-9a-f]{40}", str(request.get("integration_head", "")))
        or request.get("integration_head") != args.integration_head
        or request.get("next_stage") != next_assignment.get("stage_id")
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(request.get("next_stage_commit_receipt_sha256", ""))
        )
    ):
        raise FenceError("Stage-bind typed request identity differs")
    contract = request.get("contract")
    expected_contract_keys = {
        "slug",
        "allowed_paths",
        "forbidden_paths",
        "locks",
        "deliverables",
        "tests",
        "failure_tests",
        "rollback_test",
        "scope_notes",
    }
    if not isinstance(contract, dict) or set(contract) != expected_contract_keys:
        raise FenceError("Stage-bind typed request contract is malformed")
    slug = contract.get("slug")
    stage_id = next_assignment.get("stage_id")
    if (
        not isinstance(slug, str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", slug)
        or next_assignment.get("branch") != f"refactor/stage/{stage_id}-{slug}"
        or Path(str(next_assignment.get("worktree_path", ""))).name
        != f"{stage_id}-{slug}"
        or next_assignment.get("worktree_registry_id") != f"stage-{stage_id}-{slug}"
    ):
        raise FenceError("Stage-bind Assignment targets differ from typed request")
    rollback = contract.get("rollback_test")
    if rollback is not None and (
        not isinstance(rollback, str) or not 1 <= len(rollback.encode()) <= 4096
    ):
        raise FenceError("Stage-bind typed request rollback test is malformed")
    for key in (
        "allowed_paths",
        "forbidden_paths",
        "locks",
        "deliverables",
        "tests",
        "failure_tests",
    ):
        values = contract.get(key)
        if (
            not isinstance(values, list)
            or (key == "allowed_paths" and not values)
            or len(values) > 256
            or any(
                not isinstance(value, str)
                or not 1 <= len(value.encode()) <= 4096
                or "\0" in value
                for value in values
            )
            or len(values) != len(set(values))
        ):
            raise FenceError(f"Stage-bind typed request {key} is malformed")
    scope_notes = contract.get("scope_notes")
    if (
        not isinstance(scope_notes, str)
        or not 1 <= len(scope_notes.encode()) <= 20_000
    ):
        raise FenceError("Stage-bind typed request scope notes are malformed")
    for key in ("allowed_paths", "forbidden_paths"):
        for value in contract[key]:
            path = PurePosixPath(value)
            if (
                value != value.strip()
                or "\\" in value
                or any(ord(character) < 32 for character in value)
                or path.is_absolute()
                or str(path) != value
                or value.startswith("-")
                or value.startswith("**")
                or value == "*"
                or any(part in {"", ".", "..", ".git"} for part in path.parts)
            ):
                raise FenceError(f"Stage-bind typed request {key} path is unsafe")
    if not PROTECTED_ASSIGNMENT_PATTERNS.issubset(set(contract["forbidden_paths"])):
        raise FenceError("Stage-bind typed request omits protected forbidden paths")
    assignment_contract = {
        "allowed_paths": next_assignment.get("allowed_paths"),
        "forbidden_paths": next_assignment.get("forbidden_paths"),
        "locks": next_assignment.get("locks"),
        "deliverables": next_assignment.get("deliverables"),
        "tests": next_assignment.get("tests"),
        "failure_tests": next_assignment.get("failure_tests"),
        "rollback_test": next_assignment.get("rollback_test"),
    }
    request_contract = {key: contract.get(key) for key in assignment_contract}
    if assignment_contract != request_contract:
        raise FenceError("Stage-bind Assignment scope differs from typed request")


def validate_stage_bind_next_record(
    assignment: dict[str, Any],
    stage: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    """Validate every host-derived next Assignment/registry field before writes."""
    issued_at = assignment.get("issued_at")
    expires_at = assignment.get("expires_at")
    if not isinstance(issued_at, str) or not isinstance(expires_at, str):
        raise FenceError("next Stage timestamps are missing")
    try:
        issued = datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FenceError("next Stage timestamps are invalid") from exc
    if (
        issued.tzinfo is None
        or expires.tzinfo is None
        or expires - issued != timedelta(days=7)
        or assignment.get("issued_from_worktree") != str(ROOT)
        or assignment.get("supersedes") is not None
        or assignment.get("parallel_peer") is not None
    ):
        raise FenceError("next Assignment host-derived fields are invalid")
    expected = {
        "registry_id": assignment["worktree_registry_id"],
        "stage_id": assignment["stage_id"],
        "assignment_id": assignment["assignment_id"],
        "owner_role": "EXECUTION_WORKER",
        "path": assignment["worktree_path"],
        "branch": assignment["branch"],
        "base_sha": args.integration_head,
        "head_sha": args.integration_head,
        "dirty_uncommitted_candidate": False,
        "locks": assignment["locks"],
        "status": "READY",
        "orchestrator": args.platform,
        "orchestration_epoch": args.epoch,
        "created_at": issued_at,
        "expires_at": expires_at,
        "remove_within_hours_after_close": 24,
    }
    if stage != expected:
        raise FenceError("next Stage registry record is not exact")


def validate_replacement_contract(
    assignment: dict[str, Any],
    fence: dict[str, Any],
    stage: dict[str, Any],
    *,
    expected_worktree_head: str | None = None,
    require_dirty_match: bool = True,
    allow_unregistered_descendant: bool = False,
) -> bool:
    assignment_id = fence.get("active_assignment_id")
    identity_invalid = any(
        (
            assignment.get("assignment_id") != assignment_id,
            assignment.get("role") != "WORKER",
            assignment.get("issued_by") != "EXECUTION_COORDINATOR",
            assignment.get("status") not in {"READY", "IN_PROGRESS"},
            assignment.get("orchestrator") != fence["platform"],
            assignment.get("orchestration_epoch") != fence["epoch"],
            assignment.get("stage_id") != stage.get("stage_id"),
            assignment.get("worktree_registry_id") != stage.get("registry_id"),
            assignment.get("branch") != stage.get("branch"),
            assignment.get("locks") != stage.get("locks"),
            assignment.get("integration_branch") != INTEGRATION_BRANCH,
            assignment.get("integration_head_at_issue")
            != fence["assignment_integration_head_at_issue"],
        )
    )
    if fence["binding_kind"] == "TAKEOVER" and assignment.get("supersedes") != fence.get(
        "previous_assignment_id"
    ):
        identity_invalid = True
    if identity_invalid:
        raise FenceError("replacement Assignment identity/fence/worktree contract is invalid")
    expected_digest = fence["immutable_contract_sha256"]
    if immutable_contract_sha256(assignment) != expected_digest:
        raise FenceError("replacement Assignment immutable contract digest has drifted")
    if fence["binding_kind"] == "TAKEOVER":
        previous = load_assignment(str(fence["previous_assignment_id"]))
        if immutable_contract_sha256(previous) != expected_digest:
            raise FenceError("previous Assignment immutable contract digest has drifted")
        drift = [
            key
            for key in IMMUTABLE_ASSIGNMENT_FIELDS
            if assignment.get(key) != previous.get(key)
        ]
        if drift:
            raise FenceError(f"replacement Assignment changed immutable fields: {', '.join(drift)}")
    if stage.get("owner_role") != "EXECUTION_WORKER":
        raise FenceError("registry replacement Worker ownership is invalid")
    if stage.get("orchestrator") != fence["platform"] or stage.get("orchestration_epoch") != fence["epoch"]:
        raise FenceError("registry replacement Worker fence is invalid")
    if stage.get("status") not in ACTIVE_STAGE_STATES:
        raise FenceError("registry replacement Worker state is invalid")
    if assignment.get("base_sha") != fence["assignment_base_sha"]:
        raise FenceError("replacement Assignment base differs from fenced issue base")
    if assignment.get("worktree_path") != stage.get("path"):
        raise FenceError("replacement Assignment path does not match registry")
    require_unexpired(assignment)
    worktree = Path(str(stage.get("path", "")))
    if worktree.is_dir():
        require_integration_repository(worktree)
    actual_head = run_git("rev-parse", "HEAD", cwd=worktree) if worktree.is_dir() else ""
    expected_head = stage.get("head_sha") if expected_worktree_head is None else expected_worktree_head
    if not worktree.is_dir() or run_git("branch", "--show-current", cwd=worktree) != stage.get("branch"):
        raise FenceError("replacement Worktree path/HEAD is invalid")
    if actual_head != expected_head:
        descendant = subprocess.run(
            [GIT_BINARY, "-C", str(worktree), "merge-base", "--is-ancestor", str(expected_head), actual_head],
            check=False,
            capture_output=True,
            text=True,
        )
        if not allow_unregistered_descendant or descendant.returncode:
            raise FenceError("replacement Worktree path/HEAD is invalid")
    ancestry = subprocess.run(
        [GIT_BINARY, "-C", str(worktree), "merge-base", "--is-ancestor", fence["assignment_base_sha"], actual_head],
        check=False,
        capture_output=True,
        text=True,
    )
    if ancestry.returncode:
        raise FenceError("Worktree HEAD does not descend from fenced Assignment base")
    actual_dirty = bool(run_git("status", "--porcelain=v1", "--untracked-files=all", cwd=worktree))
    if require_dirty_match and stage.get("dirty_uncommitted_candidate") is not actual_dirty:
        raise FenceError("replacement Worktree dirty state differs from registry")
    return actual_dirty


def reconcile_takeover(fence: dict[str, Any], replacement_assignment: str) -> None:
    if fence.get("binding_kind") != "TAKEOVER":
        raise FenceError("takeover reconciliation requires TAKEOVER binding kind")
    previous_assignment = fence.get("previous_assignment_id")
    if not previous_assignment:
        raise FenceError("RECONCILING fence omits previous Assignment")
    registry = load_yaml(REGISTRY)
    registry_assignment = (registry.get("integration") or {}).get("active_assignment_id")
    if registry_assignment not in {previous_assignment, replacement_assignment}:
        raise FenceError("registry is neither before nor inside the declared reconciliation")
    verify_old_worker_idle(registry, registry_assignment, fence["integration_head_sha"])
    matches = [
        item
        for item in registry.get("stage_worktrees", [])
        if item.get("assignment_id") == registry_assignment
    ]
    stage = matches[0]
    previous_path = LOCAL / "assignments" / f"{previous_assignment}.md"
    previous_source = previous_path.read_text(encoding="utf-8")
    previous_payload = load_assignment(previous_assignment)
    if registry_assignment == previous_assignment:
        validate_prior_takeover_contract(
            previous_payload,
            previous_assignment,
            stage,
            allow_superseded=True,
        )
    if immutable_contract_sha256(previous_payload) != fence["immutable_contract_sha256"]:
        raise FenceError("previous Assignment immutable contract digest has drifted")

    issued_at = fence["claimed_at_utc"]
    old_document = assignment_document(
        previous_source,
        previous_assignment,
        {"status": "SUPERSEDED"},
        f"Superseded by `{replacement_assignment}` at orchestration epoch `{fence['epoch']}`.",
    )
    replacement_document = assignment_document(
        previous_source,
        replacement_assignment,
        {
            "assignment_id": replacement_assignment,
            "status": "READY",
            "orchestrator": fence["platform"],
            "orchestration_epoch": fence["epoch"],
            "supersedes": previous_assignment,
            "base_sha": stage["head_sha"],
            "issued_by": "EXECUTION_COORDINATOR",
            "issued_at": issued_at,
            "integration_branch": INTEGRATION_BRANCH,
            "integration_head_at_issue": fence["integration_head_sha"],
        },
        f"Automatically supersedes `{previous_assignment}`; scope, locks, external-access boundary and deliverables are preserved.",
    )
    replacement_match = re.search(r"```yaml\n(.*?)\n```", replacement_document, re.S)
    if not replacement_match:
        raise FenceError("generated replacement Assignment has no YAML contract")
    replacement_payload = yaml.safe_load(replacement_match.group(1))
    if not isinstance(replacement_payload, dict):
        raise FenceError("generated replacement Assignment YAML must be an object")
    validate_canonical_assignment(replacement_payload)
    if immutable_contract_sha256(replacement_payload) != fence["immutable_contract_sha256"]:
        raise FenceError("generated replacement Assignment changed immutable contract")

    integration = registry.get("integration") or {}
    integration.update(
        {
            "path": str(ROOT),
            "branch": INTEGRATION_BRANCH,
            "owner_role": "EXECUTION_COORDINATOR",
            "orchestrator": fence["platform"],
            "orchestration_epoch": fence["epoch"],
            "head_sha": fence["integration_head_sha"],
            "active_assignment_id": replacement_assignment,
        }
    )
    stage.update(
        {
            "assignment_id": replacement_assignment,
            "owner_role": "EXECUTION_WORKER",
            "orchestrator": fence["platform"],
            "orchestration_epoch": fence["epoch"],
            "base_sha": stage["head_sha"],
            "dirty_uncommitted_candidate": False,
        }
    )
    registry["updated_at"] = issued_at

    candidate_fence = dict(fence)
    candidate_fence["active_assignment_id"] = replacement_assignment
    candidate_fence["status"] = "RECONCILING"
    validate_fence(candidate_fence)
    validate_replacement_contract(replacement_payload, candidate_fence, stage)

    replacement_path = LOCAL / "assignments" / f"{replacement_assignment}.md"
    if replacement_path.exists() and replacement_path.read_text(encoding="utf-8") != replacement_document:
        raise FenceError("existing replacement Assignment differs from the recoverable transaction")

    atomic_write_text(previous_path, old_document)
    atomic_write_text(replacement_path, replacement_document)
    atomic_write_yaml(REGISTRY, registry)

    written_registry = load_yaml(REGISTRY)
    written_stages = [
        item
        for item in written_registry.get("stage_worktrees", [])
        if item.get("assignment_id") == replacement_assignment
    ]
    if len(written_stages) != 1:
        raise FenceError("written registry does not contain the replacement Assignment")
    written_assignment = load_assignment(replacement_assignment)
    validate_replacement_contract(written_assignment, candidate_fence, written_stages[0])
    candidate_fence["status"] = "ACTIVE"
    atomic_write_fence(candidate_fence)


def run_antigravity_validator(instance: str, *, runtime: bool) -> None:
    command = [
        sys.executable,
        "-I",
        str(ROOT / "scripts" / "validate_antigravity_two_site_adapter.py"),
    ]
    if runtime:
        command.append("--runtime")
    command.extend(["--execution-conversation", instance])
    runtime = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if runtime.returncode:
        detail = (runtime.stderr or runtime.stdout).strip()
        kind = "runtime discovery" if "--runtime" in command else "execution Coordinator binding"
        raise FenceError(f"Antigravity {kind} is not ready: {detail}")


def validate_antigravity_runtime(instance: str) -> None:
    run_antigravity_validator(instance, runtime=True)


def validate_antigravity_execution(instance: str) -> None:
    run_antigravity_validator(instance, runtime=False)


def claim(args: argparse.Namespace) -> None:
    with locked():
        validate_coordinator_instance(args.platform, args.instance)
        for label, value in (
            ("previous", args.previous_assignment),
            ("replacement", args.replacement_assignment),
        ):
            if not re.fullmatch(ASSIGNMENT_ID_PATTERN, value):
                raise FenceError(f"{label} Assignment ID is invalid")
        if args.replacement_assignment == args.previous_assignment:
            raise FenceError("replacement Assignment must have a new ID")
        if (LOCAL / "assignments" / f"{args.replacement_assignment}.md").exists():
            raise FenceError("replacement Assignment already exists")
        verify_git(args.integration_head)
        if run_git("status", "--porcelain=v1", "--untracked-files=all"):
            raise FenceError("integration worktree must be clean before takeover")
        registry = load_yaml(REGISTRY)
        stage = verify_old_worker_idle(registry, args.previous_assignment, args.integration_head)
        previous_assignment = load_assignment(args.previous_assignment)
        validate_prior_takeover_contract(previous_assignment, args.previous_assignment, stage)
        previous = load_fence(required=False)
        previous_epoch = 0 if previous is None else int(previous["epoch"])
        if previous_epoch != args.expected_epoch:
            raise FenceError("expected fence epoch does not match current state")
        if previous is not None and previous["status"] in {"ACTIVE", "RECONCILING"}:
            raise FenceError("an ACTIVE/RECONCILING orchestrator must be released or recovered before takeover")
        if args.platform == "ANTIGRAVITY":
            validate_antigravity_runtime(args.instance)
        supersedes = None
        if previous is not None:
            supersedes = {
                "epoch": previous["epoch"],
                "platform": previous["platform"],
                "coordinator_instance_id": previous["coordinator_instance_id"],
            }
        payload = {
            "schema_version": "1.0",
            "epoch": previous_epoch + 1,
            "status": "RECONCILING",
            "binding_kind": "TAKEOVER",
            "platform": args.platform,
            "coordinator_instance_id": args.instance,
            "integration_branch": INTEGRATION_BRANCH,
            "integration_head_sha": args.integration_head,
            "previous_assignment_id": args.previous_assignment,
            "active_assignment_id": None,
            "assignment_base_sha": stage["head_sha"],
            "assignment_integration_head_at_issue": args.integration_head,
            "immutable_contract_sha256": immutable_contract_sha256(previous_assignment),
            "claimed_at_utc": datetime.now(timezone.utc).isoformat(),
            "supersedes": supersedes,
        }
        atomic_write_fence(payload)
        reconcile_takeover(payload, args.replacement_assignment)
        print(f"ORCHESTRATOR_FENCE=ACTIVE epoch={payload['epoch']}")


def activate(args: argparse.Namespace) -> None:
    with locked():
        fence = load_fence()
        assert fence is not None
        if fence["status"] != "RECONCILING":
            raise FenceError("fence is not RECONCILING")
        for key, expected in (
            ("platform", args.platform),
            ("coordinator_instance_id", args.instance),
            ("epoch", args.epoch),
            ("integration_head_sha", args.integration_head),
        ):
            if fence[key] != expected:
                raise FenceError(f"fence {key} mismatch")
        if args.platform == "ANTIGRAVITY":
            validate_antigravity_execution(args.instance)
        verify_git(args.integration_head)
        if fence.get("active_assignment_id") not in (None, args.assignment):
            raise FenceError("RECONCILING fence replacement Assignment mismatch")
        reconcile_takeover(fence, args.assignment)
        print(f"ORCHESTRATOR_FENCE=ACTIVE epoch={args.epoch}")


def active_records(
    args: argparse.Namespace,
    fence: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    verify_git(args.integration_head)
    if run_git("status", "--porcelain=v1", "--untracked-files=all"):
        raise FenceError("integration worktree differs from ACTIVE fence")
    expected = {
        "status": "ACTIVE",
        "platform": args.platform,
        "coordinator_instance_id": args.instance,
        "epoch": args.epoch,
        "integration_head_sha": args.integration_head,
        "active_assignment_id": args.assignment,
    }
    for key, value in expected.items():
        if fence.get(key) != value:
            raise FenceError(f"ACTIVE fence {key} mismatch")
    if args.platform == "ANTIGRAVITY":
        validate_antigravity_execution(args.instance)
    registry = load_yaml(REGISTRY)
    integration = registry.get("integration") or {}
    if integration.get("head_sha") != args.integration_head:
        raise FenceError("registry integration head does not match ACTIVE fence")
    if integration.get("active_assignment_id") != args.assignment:
        raise FenceError("registry Assignment does not match ACTIVE fence")
    if integration.get("orchestrator") != args.platform or integration.get("orchestration_epoch") != args.epoch:
        raise FenceError("registry owner does not match ACTIVE fence")
    assignment = load_assignment(args.assignment)
    stages = [item for item in registry.get("stage_worktrees", []) if item.get("assignment_id") == args.assignment]
    if len(stages) != 1:
        raise FenceError("registry Worker does not match ACTIVE fence")
    return registry, assignment, stages[0]


def check(args: argparse.Namespace) -> None:
    with locked():
        fence = load_fence()
        assert fence is not None
        _, assignment, stage = active_records(args, fence)
        validate_replacement_contract(assignment, fence, stage)
        print(f"ORCHESTRATOR_FENCE=VALID epoch={args.epoch}")


def worker_check(args: argparse.Namespace) -> None:
    with locked():
        fence = load_fence()
        assert fence is not None
        _, assignment, stage = active_records(args, fence)
        dirty = validate_replacement_contract(
            assignment,
            fence,
            stage,
            require_dirty_match=False,
            allow_unregistered_descendant=True,
        )
        print(f"ORCHESTRATOR_WORKER_FENCE=VALID epoch={args.epoch} dirty={str(dirty).lower()}")


def stage_advance(args: argparse.Namespace) -> None:
    with locked():
        fence = load_fence()
        assert fence is not None
        registry, assignment, stage = active_records(args, fence)
        if not re.fullmatch(r"[0-9a-f]{40}", args.stage_head):
            raise FenceError("new Stage HEAD is invalid")
        dirty = validate_replacement_contract(
            assignment,
            fence,
            stage,
            expected_worktree_head=args.stage_head,
            require_dirty_match=False,
        )
        if dirty:
            raise FenceError("cannot advance a dirty Stage worktree")
        worktree = Path(str(stage["path"]))
        ancestry = subprocess.run(
            [GIT_BINARY, "-C", str(worktree), "merge-base", "--is-ancestor", str(stage["head_sha"]), args.stage_head],
            check=False,
            capture_output=True,
            text=True,
        )
        if ancestry.returncode:
            raise FenceError("new Stage HEAD does not descend from registered Stage HEAD")
        stage["head_sha"] = args.stage_head
        stage["dirty_uncommitted_candidate"] = False
        stage["status"] = "COMPLETE_CANDIDATE"
        registry["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_yaml(REGISTRY, registry)
        print(f"ORCHESTRATOR_STAGE=ADVANCED epoch={args.epoch} head={args.stage_head}")


def stage_open_check(args: argparse.Namespace) -> None:
    with locked():
        fence = load_fence()
        assert fence is not None
        _, assignment, stage = active_records(args, fence)
        validate_replacement_contract(assignment, fence, stage)
        order = load_yaml(EXECUTION_ORDER)
        stages = order.get("stages") or {}
        validate_automatic_stage(
            stages,
            str(assignment.get("stage_id", "")),
            args.next_stage,
            expected_state="PROPOSED",
        )
        print(
            f"ORCHESTRATOR_STAGE_OPEN=VALID epoch={args.epoch} "
            f"next_stage={args.next_stage}"
        )


def stage_bind(args: argparse.Namespace) -> None:
    with locked():
        for label, value in (("current", args.assignment), ("next", args.next_assignment)):
            if not re.fullmatch(ASSIGNMENT_ID_PATTERN, value):
                raise FenceError(f"{label} Assignment ID is invalid")
        fence = load_fence()
        assert fence is not None
        if fence.get("platform") != args.platform or fence.get("coordinator_instance_id") != args.instance:
            raise FenceError("Stage bind owner mismatch")
        if fence.get("epoch") != args.epoch or fence.get("integration_head_sha") != args.integration_head:
            raise FenceError("Stage bind epoch/integration mismatch")
        if args.platform == "ANTIGRAVITY":
            validate_antigravity_execution(args.instance)
        verify_git(args.integration_head)
        if run_git("status", "--porcelain=v1", "--untracked-files=all"):
            raise FenceError("integration worktree must be clean before Stage bind")

        registry = load_yaml(REGISTRY)
        integration = registry.get("integration") or {}
        if fence["status"] == "ACTIVE":
            if fence.get("active_assignment_id") != args.assignment:
                raise FenceError("ACTIVE fence current Assignment does not match Stage bind")
            active_assignment = validate_stage_bind_integration(
                integration, args, {args.assignment}
            )
            expected_prior_status = "COMPLETE_CANDIDATE"
            registry_transition_required = True
            candidate_fence = dict(fence)
            candidate_fence.update(
                {
                    "status": "RECONCILING",
                    "binding_kind": "STAGE",
                    "previous_assignment_id": args.assignment,
                    "active_assignment_id": args.next_assignment,
                    "claimed_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            )
        elif fence["status"] == "RECONCILING" and fence.get("binding_kind") == "STAGE":
            if fence.get("previous_assignment_id") != args.assignment:
                raise FenceError("recovering Stage bind previous Assignment mismatch")
            if fence.get("active_assignment_id") != args.next_assignment:
                raise FenceError("recovering Stage bind next Assignment mismatch")
            active_assignment = validate_stage_bind_integration(
                integration, args, {args.assignment, args.next_assignment}
            )
            expected_prior_status = (
                "COMPLETE_CANDIDATE"
                if active_assignment == args.assignment
                else "CLOSED"
            )
            registry_transition_required = active_assignment == args.assignment
            candidate_fence = dict(fence)
        else:
            raise FenceError("Stage bind requires ACTIVE or matching RECONCILING/STAGE fence")

        current_stages = [
            item
            for item in registry.get("stage_worktrees", [])
            if item.get("assignment_id") == args.assignment
        ]
        if len(current_stages) != 1:
            raise FenceError("current Stage is not uniquely registered")
        current_assignment = load_assignment(args.assignment)
        validate_stage_bind_prior_contract(
            current_assignment,
            args.assignment,
            current_stages[0],
            args,
            expected_status=expected_prior_status,
        )

        next_assignment = load_assignment(args.next_assignment)
        validate_canonical_assignment(next_assignment)
        validate_stage_bind_request(args, next_assignment)
        if next_assignment.get("external_access") != "NONE":
            raise FenceError("automatic Stage bind permits only external_access NONE")
        if next_assignment.get("integration_head_at_issue") != args.integration_head:
            raise FenceError("next Assignment was not issued from current integration HEAD")
        if next_assignment.get("base_sha") != args.integration_head:
            raise FenceError("next Assignment base is not the current integration HEAD")
        next_stages = [
            item
            for item in registry.get("stage_worktrees", [])
            if item.get("assignment_id") == args.next_assignment
        ]
        if len(next_stages) != 1 or next_stages[0].get("status") != "READY":
            raise FenceError("next Stage must be uniquely registered as READY")
        next_stage = next_stages[0]
        if next_stage.get("base_sha") != args.integration_head:
            raise FenceError("next Stage registry base is not the current integration HEAD")
        if next_stage.get("head_sha") != args.integration_head:
            raise FenceError("next Stage worktree is not at the current integration HEAD")
        validate_stage_bind_next_record(next_assignment, next_stage, args)
        order = load_yaml(EXECUTION_ORDER)
        stages = order.get("stages") or {}
        current_stage_id = current_assignment.get("stage_id")
        next_stage_id = next_assignment.get("stage_id")
        validate_automatic_stage(
            stages,
            str(current_stage_id or ""),
            str(next_stage_id or ""),
            expected_state="IN_PROGRESS",
        )
        candidate_fence["assignment_base_sha"] = str(next_assignment.get("base_sha", ""))
        candidate_fence["assignment_integration_head_at_issue"] = str(
            next_assignment.get("integration_head_at_issue", "")
        )
        candidate_fence["immutable_contract_sha256"] = immutable_contract_sha256(next_assignment)
        validate_fence(candidate_fence)
        dirty = validate_replacement_contract(next_assignment, candidate_fence, next_stage)
        if dirty:
            raise FenceError("next Stage worktree must be clean")

        atomic_write_fence(candidate_fence)
        if registry_transition_required:
            for item in registry.get("stage_worktrees", []):
                if item.get("assignment_id") == args.assignment:
                    item["status"] = "CLOSED"
                    item["closed_at"] = datetime.now(timezone.utc).isoformat()
            integration["active_assignment_id"] = args.next_assignment
            registry["updated_at"] = datetime.now(timezone.utc).isoformat()
            atomic_write_yaml(REGISTRY, registry)
        candidate_fence["status"] = "ACTIVE"
        atomic_write_fence(candidate_fence)
        print(f"ORCHESTRATOR_STAGE=BOUND epoch={args.epoch} assignment={args.next_assignment}")


def release(args: argparse.Namespace) -> None:
    with locked():
        fence = load_fence()
        assert fence is not None
        _, assignment, stage = active_records(args, fence)
        validate_replacement_contract(assignment, fence, stage)
        if run_git("status", "--porcelain=v1", "--untracked-files=all"):
            raise FenceError("cannot release fence from a dirty integration worktree")
        fence["status"] = "RELEASED"
        atomic_write_fence(fence)
        print(f"ORCHESTRATOR_FENCE=RELEASED epoch={args.epoch}")


def advance(args: argparse.Namespace) -> None:
    with locked():
        fence = load_fence()
        assert fence is not None
        if fence.get("status") not in {"ACTIVE", "RECONCILING"}:
            raise FenceError("only an ACTIVE or interrupted RECONCILING fence can advance")
        for key, expected in (
            ("platform", args.platform),
            ("coordinator_instance_id", args.instance),
            ("epoch", args.epoch),
            ("active_assignment_id", args.assignment),
        ):
            if fence.get(key) != expected:
                raise FenceError(f"cannot advance fence: {key} mismatch")
        if args.platform == "ANTIGRAVITY":
            validate_antigravity_execution(args.instance)
        verify_git(args.integration_head)
        parent = run_git("rev-parse", f"{args.integration_head}^")
        if fence["integration_head_sha"] not in {parent, args.integration_head}:
            raise FenceError("new integration head is not a direct child of fenced head")
        if run_git("status", "--porcelain=v1", "--untracked-files=all"):
            raise FenceError("cannot advance fence from a dirty integration worktree")
        registry = load_yaml(REGISTRY)
        integration = registry.get("integration") or {}
        if integration.get("head_sha") not in {parent, args.integration_head}:
            raise FenceError("registry cannot advance from a different integration head")
        if integration.get("active_assignment_id") != args.assignment:
            raise FenceError("registry Assignment does not match advancing fence")
        if integration.get("orchestrator") != args.platform or integration.get("orchestration_epoch") != args.epoch:
            raise FenceError("registry owner does not match advancing fence")
        assignment = load_assignment(args.assignment)
        stages = [
            item
            for item in registry.get("stage_worktrees", [])
            if item.get("assignment_id") == args.assignment
        ]
        if len(stages) != 1:
            raise FenceError("registry Worker does not match advancing fence")
        validate_replacement_contract(assignment, fence, stages[0])
        fence["status"] = "RECONCILING"
        atomic_write_fence(fence)
        integration["head_sha"] = args.integration_head
        registry["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_yaml(REGISTRY, registry)
        fence["integration_head_sha"] = args.integration_head
        fence["status"] = "ACTIVE"
        atomic_write_fence(fence)
        print(f"ORCHESTRATOR_FENCE=ADVANCED epoch={args.epoch}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    claim_parser = sub.add_parser("claim")
    claim_parser.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    claim_parser.add_argument("--instance", required=True)
    claim_parser.add_argument("--integration-head", required=True)
    claim_parser.add_argument("--previous-assignment", required=True)
    claim_parser.add_argument("--replacement-assignment", required=True)
    claim_parser.add_argument("--expected-epoch", type=int, required=True)
    activate_parser = sub.add_parser("activate")
    check_parser = sub.add_parser("check")
    worker_check_parser = sub.add_parser("worker-check")
    stage_advance_parser = sub.add_parser("stage-advance")
    stage_open_check_parser = sub.add_parser("stage-open-check")
    stage_bind_parser = sub.add_parser("stage-bind")
    release_parser = sub.add_parser("release")
    advance_parser = sub.add_parser("advance")
    for item in (
        activate_parser,
        check_parser,
        worker_check_parser,
        stage_advance_parser,
        stage_open_check_parser,
        stage_bind_parser,
        release_parser,
        advance_parser,
    ):
        item.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
        item.add_argument("--instance", required=True)
        item.add_argument("--epoch", type=int, required=True)
        item.add_argument("--integration-head", required=True)
        item.add_argument("--assignment", required=True)
    stage_advance_parser.add_argument("--stage-head", required=True)
    stage_open_check_parser.add_argument("--next-stage", required=True)
    stage_bind_parser.add_argument("--next-assignment", required=True)
    stage_bind_parser.add_argument("--request-id", required=True)
    stage_bind_parser.add_argument("--request-sha256", required=True)
    return result


def main() -> int:
    if not sys.flags.isolated:
        print("BLOCKED: fence CLI requires /usr/bin/python3 -I")
        return 1
    args = parser().parse_args()
    try:
        {
            "claim": claim,
            "activate": activate,
            "check": check,
            "worker-check": worker_check,
            "stage-advance": stage_advance,
            "stage-open-check": stage_open_check,
            "stage-bind": stage_bind,
            "release": release,
            "advance": advance,
        }[args.command](args)
    except FenceError as exc:
        print(f"BLOCKED: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
