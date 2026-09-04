#!/usr/bin/env python3
"""Atomically fence Cursor/Antigravity ownership of two-site repository writes."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml


ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local" / "two-site-refactor"
FENCE = LOCAL / "orchestrator-fence.json"
LOCK = LOCAL / "orchestrator-fence.lock"
REGISTRY = LOCAL / "worktrees.yaml"
EXECUTION_ORDER = ROOT / "docs" / "refactor" / "two-server" / "execution-order.yaml"
INTEGRATION_BRANCH = "refactor/two-site-architecture"
PLATFORMS = {"CURSOR", "ANTIGRAVITY"}
NON_RUNNING = {"IDLE", "IDLE_INTEGRATED", "BLOCKED", "READY", "COMPLETE_CANDIDATE"}
ACTIVE_STAGE_STATES = {"READY", "RUNNING", "IDLE_INTEGRATED", "COMPLETE_CANDIDATE", "BLOCKED"}
ASSIGNMENT_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]*"
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


class FenceError(RuntimeError):
    pass


def run_git(*args: str, cwd: Path | None = None) -> str:
    cwd = ROOT if cwd is None else cwd
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
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
    if not isinstance(payload["coordinator_instance_id"], str) or not payload["coordinator_instance_id"]:
        raise FenceError("orchestrator fence instance ID is invalid")
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
    if run_git("status", "--porcelain=v1", "--untracked-files=all", cwd=worktree):
        raise FenceError("registered Stage worktree is not clean")
    if run_git("rev-parse", "HEAD", cwd=worktree) != item.get("head_sha"):
        raise FenceError("registered Stage worktree HEAD mismatch")
    return item


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
    actual_head = run_git("rev-parse", "HEAD", cwd=worktree) if worktree.is_dir() else ""
    expected_head = stage.get("head_sha") if expected_worktree_head is None else expected_worktree_head
    if not worktree.is_dir() or run_git("branch", "--show-current", cwd=worktree) != stage.get("branch"):
        raise FenceError("replacement Worktree path/HEAD is invalid")
    if actual_head != expected_head:
        descendant = subprocess.run(
            ["git", "-C", str(worktree), "merge-base", "--is-ancestor", str(expected_head), actual_head],
            check=False,
            capture_output=True,
            text=True,
        )
        if not allow_unregistered_descendant or descendant.returncode:
            raise FenceError("replacement Worktree path/HEAD is invalid")
    ancestry = subprocess.run(
        ["git", "-C", str(worktree), "merge-base", "--is-ancestor", fence["assignment_base_sha"], actual_head],
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
    if previous_payload.get("assignment_id") != previous_assignment:
        raise FenceError("previous Assignment identity is invalid")
    if immutable_contract_sha256(previous_payload) != fence["immutable_contract_sha256"]:
        raise FenceError("previous Assignment immutable contract digest has drifted")

    issued_at = datetime.now(timezone.utc).isoformat()
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
    if immutable_contract_sha256(replacement_payload) != fence["immutable_contract_sha256"]:
        raise FenceError("generated replacement Assignment changed immutable contract")

    integration = registry.get("integration") or {}
    integration.update(
        {
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

    atomic_write_text(previous_path, old_document)
    atomic_write_text(LOCAL / "assignments" / f"{replacement_assignment}.md", replacement_document)
    atomic_write_yaml(REGISTRY, registry)
    fence["active_assignment_id"] = replacement_assignment
    fence["status"] = "ACTIVE"
    atomic_write_fence(fence)


def validate_antigravity_runtime() -> None:
    runtime = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_antigravity_two_site_adapter.py"), "--runtime"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if runtime.returncode:
        detail = (runtime.stderr or runtime.stdout).strip()
        raise FenceError(f"Antigravity runtime discovery is not ready: {detail}")


def claim(args: argparse.Namespace) -> None:
    with locked():
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
        require_unexpired(previous_assignment)
        previous = load_fence(required=False)
        previous_epoch = 0 if previous is None else int(previous["epoch"])
        if previous_epoch != args.expected_epoch:
            raise FenceError("expected fence epoch does not match current state")
        if previous is not None and previous["status"] in {"ACTIVE", "RECONCILING"}:
            raise FenceError("an ACTIVE/RECONCILING orchestrator must be released or recovered before takeover")
        if args.platform == "ANTIGRAVITY":
            validate_antigravity_runtime()
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
            ["git", "-C", str(worktree), "merge-base", "--is-ancestor", str(stage["head_sha"]), args.stage_head],
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
        verify_git(args.integration_head)
        if run_git("status", "--porcelain=v1", "--untracked-files=all"):
            raise FenceError("integration worktree must be clean before Stage bind")

        registry = load_yaml(REGISTRY)
        integration = registry.get("integration") or {}
        if integration.get("head_sha") != args.integration_head:
            raise FenceError("registry integration head does not match Stage bind")
        if integration.get("orchestrator") != args.platform or integration.get("orchestration_epoch") != args.epoch:
            raise FenceError("registry owner does not match Stage bind")

        if fence["status"] == "ACTIVE":
            if fence.get("active_assignment_id") != args.assignment:
                raise FenceError("ACTIVE fence current Assignment does not match Stage bind")
            if integration.get("active_assignment_id") != args.assignment:
                raise FenceError("registry current Assignment does not match Stage bind")
            current_stages = [
                item
                for item in registry.get("stage_worktrees", [])
                if item.get("assignment_id") == args.assignment
            ]
            if len(current_stages) != 1:
                raise FenceError("current Stage is not uniquely registered")
            current_assignment = load_assignment(args.assignment)
            validate_replacement_contract(current_assignment, fence, current_stages[0])
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
            if integration.get("active_assignment_id") not in {args.assignment, args.next_assignment}:
                raise FenceError("registry is outside the recovering Stage bind")
            candidate_fence = dict(fence)
        else:
            raise FenceError("Stage bind requires ACTIVE or matching RECONCILING/STAGE fence")

        next_assignment = load_assignment(args.next_assignment)
        if next_assignment.get("external_access") != "NONE":
            raise FenceError("automatic Stage bind permits only external_access NONE")
        if next_assignment.get("integration_head_at_issue") != args.integration_head:
            raise FenceError("next Assignment was not issued from current integration HEAD")
        next_stages = [
            item
            for item in registry.get("stage_worktrees", [])
            if item.get("assignment_id") == args.next_assignment
        ]
        if len(next_stages) != 1 or next_stages[0].get("status") != "READY":
            raise FenceError("next Stage must be uniquely registered as READY")
        next_stage = next_stages[0]
        order = load_yaml(EXECUTION_ORDER)
        stages = order.get("stages") or {}
        current_stage_id = load_assignment(args.assignment).get("stage_id")
        next_stage_id = next_assignment.get("stage_id")
        current_state = stages.get(current_stage_id) or {}
        next_state = stages.get(next_stage_id) or {}
        if current_state.get("state") != "COMPLETE":
            raise FenceError("current Stage is not COMPLETE in canonical execution order")
        if next_state.get("state") != "IN_PROGRESS":
            raise FenceError("next Stage is not IN_PROGRESS in canonical execution order")
        if next_state.get("production") is True or next_state.get("destructive") is True:
            raise FenceError("production/destructive Stage cannot use automatic Stage bind")
        dependencies = next_state.get("depends_on")
        if not isinstance(dependencies, list) or any(
            (stages.get(dependency) or {}).get("state") != "COMPLETE"
            for dependency in dependencies
        ):
            raise FenceError("next Stage dependencies are not all COMPLETE")
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
    stage_bind_parser = sub.add_parser("stage-bind")
    release_parser = sub.add_parser("release")
    advance_parser = sub.add_parser("advance")
    for item in (
        activate_parser,
        check_parser,
        worker_check_parser,
        stage_advance_parser,
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
    stage_bind_parser.add_argument("--next-assignment", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        {
            "claim": claim,
            "activate": activate,
            "check": check,
            "worker-check": worker_check,
            "stage-advance": stage_advance,
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
