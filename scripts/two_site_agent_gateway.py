"""Fail-closed planner for the two-site Antigravity typed-operation gateway.

The module deliberately performs no command execution and no filesystem mutation.
A protected controller supplies caller identity, Git observations and independently
validated receipt bindings.  The planner reads canonical repository contracts and
returns immutable, fixed-argv command plans for an isolated executor.

This is not a generic shell gateway.  Unsupported workflows (including arbitrary
pytest flags, arbitrary Git ranges, multi-commit initial integration and external
actions other than the already-authorized Codex review bridge) fail closed.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import stat
import sys
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Mapping, Protocol, Sequence

import yaml


SHA40 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
ASSIGNMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
STAGE_ID = re.compile(r"[A-Z][A-Z0-9]*-[0-9]+[a-z]?")
TEST_FILE = re.compile(r"tests/(?:[A-Za-z0-9_.-]+/)*test[A-Za-z0-9_.-]*\.py")
TEST_NODE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
INTEGRATION_BRANCH = "refactor/two-site-architecture"
REVIEW_AUTHORIZATION = "TWO_SITE_REFACTOR_SOURCE_AND_EVIDENCE_TO_OPENAI_CODEX"
RESULTING_HEAD = "${CONTROLLER_OBSERVED_RESULTING_HEAD}"

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


class FenceAPI(Protocol):
    """Pure subset of scripts/two_site_orchestrator_fence.py used here."""

    def validate_fence(self, payload: Any) -> None: ...

    def validate_canonical_assignment(self, assignment: dict[str, Any]) -> None: ...

    def immutable_contract_sha256(self, assignment: dict[str, Any]) -> str: ...


class GatewayErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNKNOWN_OPERATION = "UNKNOWN_OPERATION"
    CALLER_MISMATCH = "CALLER_MISMATCH"
    ROLE_FORBIDDEN = "ROLE_FORBIDDEN"
    STATE_MISMATCH = "STATE_MISMATCH"
    PATH_REJECTED = "PATH_REJECTED"
    SYMLINK_REJECTED = "SYMLINK_REJECTED"
    EXTERNAL_ACTION_REJECTED = "EXTERNAL_ACTION_REJECTED"
    RECEIPT_MISMATCH = "RECEIPT_MISMATCH"
    PATCH_MISMATCH = "PATCH_MISMATCH"
    COMMIT_GUARD_REJECTED = "COMMIT_GUARD_REJECTED"
    UNSUPPORTED_SAFE_WORKFLOW = "UNSUPPORTED_SAFE_WORKFLOW"


class GatewayPlanError(ValueError):
    """Typed fail-closed planning error."""

    def __init__(self, code: GatewayErrorCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message


class ActorRole(str, Enum):
    COORDINATOR = "COORDINATOR"
    WORKER = "WORKER"
    VERIFIER = "VERIFIER"


class EnvClass(str, Enum):
    READ_ONLY = "READ_ONLY"
    FENCE = "FENCE"
    HERMETIC_TEST = "HERMETIC_TEST"
    GIT_MUTATION = "GIT_MUTATION"
    EXTERNAL_CODEX_REVIEW = "EXTERNAL_CODEX_REVIEW"
    NO_EXECUTION = "NO_EXECUTION"


class ReceiptKind(str, Enum):
    VERIFIER = "VERIFIER"
    CODEX_FINAL = "CODEX_FINAL"
    CODEX_CLOSEOUT_PREACTION = "CODEX_CLOSEOUT_PREACTION"
    CLOSEOUT_COMMIT = "CLOSEOUT_COMMIT"
    NEXT_STAGE_PREPARE = "NEXT_STAGE_PREPARE"
    NEXT_STAGE_COMMIT = "NEXT_STAGE_COMMIT"


RECEIPT_DECISIONS = {
    ReceiptKind.VERIFIER: "VERIFIED",
    ReceiptKind.CODEX_FINAL: "APPROVE",
    ReceiptKind.CODEX_CLOSEOUT_PREACTION: "APPROVE",
    ReceiptKind.CLOSEOUT_COMMIT: "VERIFIED",
    ReceiptKind.NEXT_STAGE_PREPARE: "PREPARED",
    ReceiptKind.NEXT_STAGE_COMMIT: "VERIFIED",
}


@dataclass(frozen=True)
class ActorBinding:
    actor_id: str
    role: ActorRole
    assignment_id: str


@dataclass(frozen=True)
class GitSnapshot:
    """Trusted, controller-observed Git state; never populated by the agent."""

    worktree: Path
    branch: str
    head_sha: str
    parent_sha: str | None = None
    staged_paths: tuple[str, ...] = ()
    unstaged_paths: tuple[str, ...] = ()
    untracked_paths: tuple[str, ...] = ()
    worktree_patch_sha256: str | None = None
    staged_patch_sha256: str | None = None

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.staged_paths + self.unstaged_paths + self.untracked_paths)))

    @property
    def clean(self) -> bool:
        return not self.changed_paths


@dataclass(frozen=True)
class ReceiptBinding:
    """Receipt identity already independently validated by the controller."""

    kind: ReceiptKind
    path: str
    stage_id: str
    assignment_id: str
    decision: str
    receipt_sha256: str
    base_sha: str | None
    head_sha: str
    patch_sha256: str | None = None
    file_sha256: str | None = None


@dataclass(frozen=True)
class CandidateBinding:
    """Controller-observed exact single correction commit for integration."""

    assignment_id: str
    candidate_sha: str
    parent_sha: str
    changed_paths: tuple[str, ...]
    integration_patch_sha256: str
    test_selectors: tuple[str, ...]
    verifier_receipt_sha256: str


@dataclass(frozen=True)
class ReviewBinding:
    """Controller-selected exact final-review range and canonical bundle."""

    assignment_id: str
    bundle: str
    base_sha: str
    head_sha: str


REVIEW_BUNDLE_FILES = (
    "assignment.md",
    "STAGE_REPORT.md",
    "FINAL_REVIEW_REQUEST.md",
    "HANDOFF.md",
    "commit.txt",
)


@dataclass(frozen=True)
class CommitGuard:
    """Trusted Git hook/config attestation required before any commit plan."""

    hook_path: Path
    hook_sha256: str
    main_memory_guard_sha256: str
    validated_at_integration_head: str
    core_hooks_path: str | None
    preserve_repository_signing: bool


@dataclass(frozen=True)
class GatewayContext:
    trusted_root: Path
    caller_id: str
    actors: tuple[ActorBinding, ...]
    mounted_workspaces: tuple[Path, ...]
    git_snapshots: tuple[GitSnapshot, ...]
    receipts: tuple[ReceiptBinding, ...]
    candidates: tuple[CandidateBinding, ...]
    reviews: tuple[ReviewBinding, ...]
    fence_api: FenceAPI | ModuleType
    source_review_authorization: str | None
    commit_guard: CommitGuard | None


@dataclass(frozen=True)
class CommandPlan:
    argv: tuple[str, ...]
    cwd: str
    env_class: EnvClass
    purpose: str
    expected_exit_codes: tuple[int, ...] = (0,)


@dataclass(frozen=True)
class GitCheckpoint:
    """State the controller must observe atomically between command steps."""

    after_command_index: int
    worktree: str
    expected_head_sha: str
    expected_staged_paths: tuple[str, ...]
    expected_staged_patch_sha256: str
    forbid_unstaged: bool
    forbid_untracked: bool


@dataclass(frozen=True)
class FollowupCommandPlan:
    """A fixed template whose sole dynamic value comes from trusted Git observation."""

    argv_template: tuple[str, ...]
    cwd: str
    env_class: EnvClass
    observed_value: str
    purpose: str


@dataclass(frozen=True)
class OperationPlan:
    request_id: str
    operation: str
    actor_id: str
    role: ActorRole
    commands: tuple[CommandPlan, ...]
    followups: tuple[FollowupCommandPlan, ...]
    checkpoints: tuple[GitCheckpoint, ...]
    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]
    bindings: tuple[tuple[str, str], ...]
    stop_on_first_failure: bool = True


@dataclass(frozen=True)
class _CanonicalState:
    root: Path
    local: Path
    fence: dict[str, Any]
    registry: dict[str, Any]
    assignment: dict[str, Any]
    stage: dict[str, Any]
    stage_worktree: Path
    integration_snapshot: GitSnapshot
    stage_snapshot: GitSnapshot
    coordinator_id: str
    actor: ActorBinding


COMMON_KEYS = {"id", "operation", "actor_id", "assignment", "epoch", "integration_head"}
OPERATION_KEYS: dict[str, set[str]] = {
    "inspect": set(),
    "status": set(),
    "diff": set(),
    "worker-check": set(),
    "test": {"selectors"},
    "verifier-submit": {"candidate_sha", "test_receipt_sha256", "test_selectors", "decision", "findings"},
    "worker-commit": {"expected_patch_sha256", "test_receipt_sha256", "message", "allowed_paths"},
    "stage-advance": {"stage_head", "worker_receipt_sha256"},
    "integrate": {
        "candidate_sha",
        "expected_patch_sha256",
        "test_selectors",
        "verifier_receipt_sha256",
        "message",
        "allowed_paths",
    },
    "bundle-prepare": {"base_sha", "head_sha", "stage_report", "final_review_request", "handoff"},
    "review-request": {"bundle", "base_sha", "head_sha"},
    "closeout-prepare": {"accepted_head", "final_receipt_sha256"},
    "closeout-review": {"accepted_head", "final_receipt_sha256"},
    "closeout-commit": {
        "accepted_head",
        "expected_patch_sha256",
        "closeout_receipt_sha256",
        "message",
    },
    "closeout-advance": {"closeout_head", "closeout_commit_receipt_sha256"},
    "next-stage-prepare": {"next_stage", "closeout_commit_receipt_sha256"},
    "next-stage-commit": {
        "next_stage", "expected_patch_sha256", "prepare_receipt_sha256", "message",
    },
    "next-stage-bind": {
        "next_stage", "next_assignment", "next_stage_commit_receipt_sha256", "contract",
    },
    "receipt-check": {"receipt_kind", "receipt_sha256", "reviewed_sha"},
}

ROLE_OPERATIONS = {
    ActorRole.COORDINATOR: {
        "inspect",
        "status",
        "diff",
        "stage-advance",
        "integrate",
        "bundle-prepare",
        "review-request",
        "closeout-prepare",
        "closeout-review",
        "closeout-commit",
        "closeout-advance",
        "next-stage-prepare",
        "next-stage-commit",
        "next-stage-bind",
        "receipt-check",
    },
    ActorRole.WORKER: {"inspect", "status", "diff", "worker-check", "test", "worker-commit"},
    ActorRole.VERIFIER: {"inspect", "status", "diff", "worker-check", "test", "verifier-submit", "receipt-check"},
}


def _fail(code: GatewayErrorCode, message: str) -> None:
    raise GatewayPlanError(code, message)


def _canonical_uuid(value: Any, label: str) -> str:
    if not isinstance(value, str):
        _fail(GatewayErrorCode.INVALID_REQUEST, f"{label} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise GatewayPlanError(GatewayErrorCode.INVALID_REQUEST, f"{label} is not a UUID") from exc
    if str(parsed) != value or parsed.version != 4:
        _fail(GatewayErrorCode.INVALID_REQUEST, f"{label} must be a canonical lowercase UUIDv4")
    return value


def _sha40(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA40.fullmatch(value):
        _fail(GatewayErrorCode.INVALID_REQUEST, f"{label} must be a full lowercase Git SHA")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        _fail(GatewayErrorCode.INVALID_REQUEST, f"{label} must be a lowercase SHA-256")
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail(GatewayErrorCode.INVALID_REQUEST, f"{label} must be a positive integer")
    return value


def _safe_relative(value: Any, label: str, *, allow_glob: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(GatewayErrorCode.PATH_REJECTED, f"{label} must be a non-empty normalized string")
    if "\\" in value or any(ord(character) < 32 for character in value):
        _fail(GatewayErrorCode.PATH_REJECTED, f"{label} contains forbidden characters")
    if not allow_glob and any(character in value for character in "*?[]"):
        _fail(GatewayErrorCode.PATH_REJECTED, f"{label} cannot contain glob syntax")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or value.startswith("-")
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _fail(GatewayErrorCode.PATH_REJECTED, f"{label} is not a safe relative path")
    if ".git" in path.parts:
        _fail(GatewayErrorCode.PATH_REJECTED, f"{label} cannot address Git metadata")
    return value


def _require_no_symlink(path: Path, *, expected: str) -> Path:
    try:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current = current / part
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                _fail(GatewayErrorCode.SYMLINK_REJECTED, f"{expected} traverses a symlink: {current}")
    except FileNotFoundError as exc:
        raise GatewayPlanError(GatewayErrorCode.PATH_REJECTED, f"{expected} is missing: {path}") from exc
    return path


def _require_directory(path: Path, label: str) -> Path:
    if not path.is_absolute():
        _fail(GatewayErrorCode.PATH_REJECTED, f"{label} must be absolute")
    _require_no_symlink(path, expected=label)
    if not path.is_dir() or path.resolve() != path:
        _fail(GatewayErrorCode.PATH_REJECTED, f"{label} is not a canonical directory")
    return path


def _read_text_file(path: Path, label: str) -> str:
    _require_no_symlink(path, expected=label)
    if not path.is_file():
        _fail(GatewayErrorCode.PATH_REJECTED, f"{label} is not a regular file")
    return path.read_text(encoding="utf-8")


def _hash_file(path: Path, label: str) -> str:
    _require_no_symlink(path, expected=label)
    if not path.is_file():
        _fail(GatewayErrorCode.PATH_REJECTED, f"{label} is not a regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_existing_relative(root: Path, relative: str, label: str, *, directory: bool = False) -> Path:
    relative = _safe_relative(relative, label)
    candidate = root / relative
    _require_no_symlink(candidate, expected=label)
    try:
        candidate.resolve(strict=True).relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise GatewayPlanError(GatewayErrorCode.PATH_REJECTED, f"{label} escapes the trusted root") from exc
    if directory and not candidate.is_dir():
        _fail(GatewayErrorCode.PATH_REJECTED, f"{label} must be a directory")
    if not directory and not candidate.is_file():
        _fail(GatewayErrorCode.PATH_REJECTED, f"{label} must be a regular file")
    return candidate


def _snapshot_for(context: GatewayContext, path: Path) -> GitSnapshot:
    matches = [item for item in context.git_snapshots if item.worktree == path]
    if len(matches) != 1:
        _fail(GatewayErrorCode.STATE_MISMATCH, f"trusted Git snapshot is not unique for {path}")
    snapshot = matches[0]
    _sha40(snapshot.head_sha, "snapshot head")
    if snapshot.parent_sha is not None:
        _sha40(snapshot.parent_sha, "snapshot parent")
    for label, paths in (
        ("staged path", snapshot.staged_paths),
        ("unstaged path", snapshot.unstaged_paths),
        ("untracked path", snapshot.untracked_paths),
    ):
        if len(set(paths)) != len(paths):
            _fail(GatewayErrorCode.STATE_MISMATCH, f"{label} list contains duplicates")
        for value in paths:
            _safe_relative(value, label)
    for label, digest in (
        ("worktree patch", snapshot.worktree_patch_sha256),
        ("staged patch", snapshot.staged_patch_sha256),
    ):
        if digest is not None:
            _sha256(digest, label)
    return snapshot


def _load_mapping(path: Path, label: str, *, json_format: bool = False) -> dict[str, Any]:
    text = _read_text_file(path, label)
    try:
        payload = json.loads(text) if json_format else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise GatewayPlanError(GatewayErrorCode.STATE_MISMATCH, f"{label} is malformed") from exc
    if not isinstance(payload, dict):
        _fail(GatewayErrorCode.STATE_MISMATCH, f"{label} root must be an object")
    return payload


def _load_assignment(path: Path) -> dict[str, Any]:
    source = _read_text_file(path, "Assignment")
    blocks = re.findall(r"```yaml\n(.*?)\n```", source, re.S)
    if len(blocks) != 1:
        _fail(GatewayErrorCode.STATE_MISMATCH, "Assignment must contain exactly one YAML contract")
    try:
        payload = yaml.safe_load(blocks[0])
    except yaml.YAMLError as exc:
        raise GatewayPlanError(GatewayErrorCode.STATE_MISMATCH, "Assignment YAML is malformed") from exc
    if not isinstance(payload, dict):
        _fail(GatewayErrorCode.STATE_MISMATCH, "Assignment YAML root must be an object")
    return payload


def _validate_stage_metadata(root: Path, assignment: dict[str, Any], operation: str) -> None:
    order_path = root / "docs" / "refactor" / "two-server" / "execution-order.yaml"
    order = _load_mapping(order_path, "execution order")
    stages = order.get("stages")
    stage_id = assignment.get("stage_id")
    if not isinstance(stages, dict) or not isinstance(stages.get(stage_id), dict):
        _fail(GatewayErrorCode.STATE_MISMATCH, "active Stage is absent from execution order")
    metadata = stages[stage_id]
    unknown = sorted(set(metadata) - STAGE_METADATA_FIELDS)
    if unknown:
        _fail(GatewayErrorCode.EXTERNAL_ACTION_REJECTED, f"Stage has unknown metadata: {', '.join(unknown)}")
    allowed_states = ({"COMPLETE"} if operation in {
        "closeout-review", "closeout-commit", "closeout-advance",
        "next-stage-prepare", "next-stage-commit", "next-stage-bind",
    } else {"IN_PROGRESS"})
    if operation == "receipt-check":
        allowed_states.add("COMPLETE")
    if metadata.get("state") not in allowed_states:
        _fail(GatewayErrorCode.STATE_MISMATCH, f"active Stage state is invalid for {operation}")
    for flag in EXTERNAL_STAGE_FLAGS:
        value = metadata.get(flag, False)
        if not isinstance(value, bool):
            _fail(GatewayErrorCode.EXTERNAL_ACTION_REJECTED, f"Stage {flag} is not boolean")
        if value:
            _fail(GatewayErrorCode.EXTERNAL_ACTION_REJECTED, f"Stage {flag} requires a separate external gate")


def _validate_pattern(pattern: Any, label: str) -> str:
    value = _safe_relative(pattern, label, allow_glob=True)
    if value.startswith("**") or value == "*":
        _fail(GatewayErrorCode.PATH_REJECTED, f"{label} is broader than a named project path")
    return value


def _validate_assignment_paths(assignment: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    allowed_raw = assignment.get("allowed_paths")
    forbidden_raw = assignment.get("forbidden_paths")
    if not isinstance(allowed_raw, list) or not isinstance(forbidden_raw, list):
        _fail(GatewayErrorCode.STATE_MISMATCH, "Assignment path contracts must be arrays")
    allowed = tuple(_validate_pattern(item, "allowed path") for item in allowed_raw)
    forbidden = tuple(_validate_pattern(item, "forbidden path") for item in forbidden_raw)
    if not allowed or len(set(allowed)) != len(allowed) or len(set(forbidden)) != len(forbidden):
        _fail(GatewayErrorCode.STATE_MISMATCH, "Assignment path contracts are empty or duplicated")
    return allowed, forbidden


def _path_is_allowed(path: str, allowed: Sequence[str], forbidden: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in allowed) and not any(
        fnmatch.fnmatchcase(path, pattern) for pattern in forbidden
    )


def _validate_changed_paths(
    worktree: Path,
    paths: Sequence[str],
    allowed: Sequence[str],
    forbidden: Sequence[str],
) -> tuple[str, ...]:
    if not paths or len(set(paths)) != len(paths):
        _fail(GatewayErrorCode.PATH_REJECTED, "changed paths must be a non-empty unique list")
    normalized = tuple(sorted(_safe_relative(item, "changed path") for item in paths))
    for relative in normalized:
        if not _path_is_allowed(relative, allowed, forbidden):
            _fail(GatewayErrorCode.PATH_REJECTED, f"changed path is outside Assignment scope: {relative}")
        # Deletions and symlink targets are deliberately unsupported in this first gateway.
        _safe_existing_relative(worktree, relative, "changed path")
    return normalized


def _load_state(request: Mapping[str, Any], context: GatewayContext) -> _CanonicalState:
    root = _require_directory(context.trusted_root, "trusted root")
    mounted = tuple(_require_directory(item, "mounted workspace") for item in context.mounted_workspaces)
    if len(set(mounted)) != len(mounted) or root not in mounted:
        _fail(GatewayErrorCode.STATE_MISMATCH, "trusted root must be uniquely mounted")

    for item in context.actors:
        _canonical_uuid(item.actor_id, "actor binding ID")
        if not isinstance(item.role, ActorRole):
            _fail(GatewayErrorCode.STATE_MISMATCH, "actor binding role is invalid")
        if not ASSIGNMENT_ID.fullmatch(item.assignment_id):
            _fail(GatewayErrorCode.STATE_MISMATCH, "actor binding Assignment is invalid")
    actors = {item.actor_id: item for item in context.actors}
    if len(actors) != len(context.actors):
        _fail(GatewayErrorCode.STATE_MISMATCH, "actor bindings contain duplicate IDs")
    caller_id = _canonical_uuid(context.caller_id, "trusted caller ID")
    if request["actor_id"] != caller_id:
        _fail(GatewayErrorCode.CALLER_MISMATCH, "request actor does not match protected caller context")
    actor = actors.get(caller_id)
    if actor is None:
        _fail(GatewayErrorCode.CALLER_MISMATCH, "protected caller has no actor binding")
    if actor.assignment_id != request["assignment"]:
        _fail(GatewayErrorCode.CALLER_MISMATCH, "protected caller is not bound to this Assignment")

    local = root / ".local" / "two-site-refactor"
    fence = _load_mapping(local / "orchestrator-fence.json", "orchestrator fence", json_format=True)
    try:
        context.fence_api.validate_fence(fence)
    except Exception as exc:
        raise GatewayPlanError(GatewayErrorCode.STATE_MISMATCH, f"canonical fence validation failed: {exc}") from exc
    if fence.get("status") != "ACTIVE" or fence.get("platform") != "ANTIGRAVITY":
        _fail(GatewayErrorCode.STATE_MISMATCH, "gateway requires an ACTIVE Antigravity fence")
    expected_fence = {
        "epoch": request["epoch"],
        "integration_head_sha": request["integration_head"],
        "active_assignment_id": request["assignment"],
    }
    for key, expected in expected_fence.items():
        if fence.get(key) != expected:
            _fail(GatewayErrorCode.STATE_MISMATCH, f"fence {key} does not match request")

    coordinators = [
        item
        for item in context.actors
        if item.role is ActorRole.COORDINATOR and item.assignment_id == request["assignment"]
    ]
    if len(coordinators) != 1 or coordinators[0].actor_id != fence.get("coordinator_instance_id"):
        _fail(GatewayErrorCode.CALLER_MISMATCH, "fence Coordinator is not uniquely bound in protected context")
    coordinator_id = coordinators[0].actor_id

    registry = _load_mapping(local / "worktrees.yaml", "worktree registry")
    integration = registry.get("integration")
    if not isinstance(integration, dict):
        _fail(GatewayErrorCode.STATE_MISMATCH, "registry integration record is missing")
    expected_integration = {
        "path": str(root),
        "branch": INTEGRATION_BRANCH,
        "head_sha": request["integration_head"],
        "active_assignment_id": request["assignment"],
        "orchestrator": "ANTIGRAVITY",
        "orchestration_epoch": request["epoch"],
    }
    for key, expected in expected_integration.items():
        if integration.get(key) != expected:
            _fail(GatewayErrorCode.STATE_MISMATCH, f"registry integration {key} mismatch")

    assignment_path = local / "assignments" / f"{request['assignment']}.md"
    assignment = _load_assignment(assignment_path)
    try:
        context.fence_api.validate_canonical_assignment(assignment)
        digest = context.fence_api.immutable_contract_sha256(assignment)
    except Exception as exc:
        raise GatewayPlanError(GatewayErrorCode.STATE_MISMATCH, f"canonical Assignment validation failed: {exc}") from exc
    if assignment.get("assignment_id") != request["assignment"]:
        _fail(GatewayErrorCode.STATE_MISMATCH, "Assignment identity mismatch")
    if assignment.get("orchestrator") != "ANTIGRAVITY" or assignment.get("orchestration_epoch") != request["epoch"]:
        _fail(GatewayErrorCode.STATE_MISMATCH, "Assignment orchestrator/epoch mismatch")
    if assignment.get("integration_head_at_issue") != fence.get("assignment_integration_head_at_issue"):
        _fail(GatewayErrorCode.STATE_MISMATCH, "Assignment integration issue SHA mismatch")
    if assignment.get("base_sha") != fence.get("assignment_base_sha"):
        _fail(GatewayErrorCode.STATE_MISMATCH, "Assignment base differs from fence")
    if digest != fence.get("immutable_contract_sha256"):
        _fail(GatewayErrorCode.STATE_MISMATCH, "Assignment immutable digest differs from fence")
    if assignment.get("external_access") != "NONE":
        _fail(GatewayErrorCode.EXTERNAL_ACTION_REJECTED, "Assignment external_access must be NONE")
    _validate_assignment_paths(assignment)
    _validate_stage_metadata(root, assignment, str(request["operation"]))

    stages = registry.get("stage_worktrees")
    if not isinstance(stages, list):
        _fail(GatewayErrorCode.STATE_MISMATCH, "registry Stage records are missing")
    matches = [item for item in stages if isinstance(item, dict) and item.get("assignment_id") == request["assignment"]]
    if len(matches) != 1:
        _fail(GatewayErrorCode.STATE_MISMATCH, "active Assignment Stage worktree is not unique")
    stage = matches[0]
    expected_stage = {
        "stage_id": assignment.get("stage_id"),
        "registry_id": assignment.get("worktree_registry_id"),
        "path": assignment.get("worktree_path"),
        "branch": assignment.get("branch"),
        "locks": assignment.get("locks"),
        "owner_role": "EXECUTION_WORKER",
        "orchestrator": "ANTIGRAVITY",
        "orchestration_epoch": request["epoch"],
    }
    for key, expected in expected_stage.items():
        if stage.get(key) != expected:
            _fail(GatewayErrorCode.STATE_MISMATCH, f"registry Stage {key} mismatch")
    stage_worktree = _require_directory(Path(str(stage["path"])), "registered Stage worktree")
    if stage_worktree not in mounted:
        _fail(GatewayErrorCode.PATH_REJECTED, "registered Stage worktree is not an exact mounted workspace")

    integration_snapshot = _snapshot_for(context, root)
    stage_snapshot = _snapshot_for(context, stage_worktree)
    expected_observed_head = (request.get("closeout_head")
                              if request["operation"] == "closeout-advance"
                              else request["integration_head"])
    if (integration_snapshot.branch != INTEGRATION_BRANCH
            or integration_snapshot.head_sha != expected_observed_head):
        _fail(GatewayErrorCode.STATE_MISMATCH, "trusted integration Git snapshot is stale")
    if (request["operation"] == "closeout-advance"
            and integration_snapshot.parent_sha != request["integration_head"]):
        _fail(GatewayErrorCode.STATE_MISMATCH, "closeout HEAD is not the direct child of the fenced head")
    if stage_snapshot.branch != assignment.get("branch"):
        _fail(GatewayErrorCode.STATE_MISMATCH, "trusted Stage Git branch is stale")

    return _CanonicalState(
        root=root,
        local=local,
        fence=fence,
        registry=registry,
        assignment=assignment,
        stage=stage,
        stage_worktree=stage_worktree,
        integration_snapshot=integration_snapshot,
        stage_snapshot=stage_snapshot,
        coordinator_id=coordinator_id,
        actor=actor,
    )


def _fence_command(state: _CanonicalState, command: str, **extra: str) -> CommandPlan:
    argv = [
        sys.executable,
        str(state.root / "scripts" / "two_site_orchestrator_fence.py"),
        command,
        "--platform",
        "ANTIGRAVITY",
        "--instance",
        state.coordinator_id,
        "--epoch",
        str(state.fence["epoch"]),
        "--integration-head",
        str(extra.pop("integration_head", state.fence["integration_head_sha"])),
        "--assignment",
        str(state.assignment["assignment_id"]),
    ]
    for key, value in extra.items():
        argv.extend((f"--{key.replace('_', '-')}", value))
    return CommandPlan(tuple(argv), str(state.root), EnvClass.FENCE, f"canonical fence {command}")


def _git_status(path: Path, purpose: str) -> CommandPlan:
    return CommandPlan(
        ("git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=all"),
        str(path),
        EnvClass.READ_ONLY,
        purpose,
    )


def _target_for_actor(state: _CanonicalState) -> Path:
    return state.root if state.actor.role is ActorRole.COORDINATOR else state.stage_worktree


def _test_selectors(value: Any, state: _CanonicalState) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        _fail(GatewayErrorCode.INVALID_REQUEST, "selectors must be a non-empty JSON string array")
    if len(set(value)) != len(value):
        _fail(GatewayErrorCode.INVALID_REQUEST, "test selectors must be unique")
    selectors: list[str] = []
    for selector in value:
        parts = selector.split("::")
        path = _safe_relative(parts[0], "test selector")
        if not TEST_FILE.fullmatch(path) or any(not TEST_NODE.fullmatch(node) for node in parts[1:]):
            _fail(GatewayErrorCode.INVALID_REQUEST, f"unsupported explicit pytest selector: {selector}")
        _safe_existing_relative(state.stage_worktree, path, "test selector")
        if not _path_is_allowed(path, *(_validate_assignment_paths(state.assignment))):
            _fail(GatewayErrorCode.PATH_REJECTED, f"test selector is outside Assignment scope: {path}")
        selectors.append(selector)
    return tuple(selectors)


def _commit_message(value: Any, stage_id: str) -> str:
    if not isinstance(value, str) or "\n" in value or "\r" in value or len(value) > 120:
        _fail(GatewayErrorCode.INVALID_REQUEST, "commit message must be one line of at most 120 characters")
    prefix = f"{stage_id}: "
    if not value.startswith(prefix) or len(value) <= len(prefix):
        _fail(GatewayErrorCode.INVALID_REQUEST, f"commit message must start with {prefix!r}")
    if any(ord(character) < 32 for character in value):
        _fail(GatewayErrorCode.INVALID_REQUEST, "commit message contains control characters")
    return value


def _validate_commit_guard(context: GatewayContext, state: _CanonicalState) -> CommitGuard:
    guard = context.commit_guard
    if guard is None:
        _fail(GatewayErrorCode.COMMIT_GUARD_REJECTED, "trusted commit-guard attestation is missing")
    if guard.core_hooks_path is not None:
        _fail(GatewayErrorCode.COMMIT_GUARD_REJECTED, "alternate core.hooksPath is forbidden")
    if not guard.preserve_repository_signing:
        _fail(GatewayErrorCode.COMMIT_GUARD_REJECTED, "repository signing policy must be preserved")
    if guard.validated_at_integration_head != state.fence["integration_head_sha"]:
        _fail(GatewayErrorCode.COMMIT_GUARD_REJECTED, "commit guard was validated at a stale integration head")
    _sha256(guard.hook_sha256, "pre-commit hook digest")
    _sha256(guard.main_memory_guard_sha256, "main memory-secret guard digest")
    hook_path = guard.hook_path
    expected_hook_path = state.root / ".git" / "hooks" / "pre-commit"
    if hook_path != expected_hook_path:
        _fail(GatewayErrorCode.COMMIT_GUARD_REJECTED, "pre-commit hook path is not the trusted repository hook")
    _require_no_symlink(hook_path, expected="pre-commit hook")
    if not hook_path.is_file() or not (hook_path.stat().st_mode & stat.S_IXUSR):
        _fail(GatewayErrorCode.COMMIT_GUARD_REJECTED, "pre-commit hook is missing or non-executable")
    if _hash_file(hook_path, "pre-commit hook") != guard.hook_sha256:
        _fail(GatewayErrorCode.COMMIT_GUARD_REJECTED, "pre-commit hook digest changed")
    expected_lines = (
        "#!/bin/sh",
        "set -eu",
        "",
        "git diff --cached --check",
        "git show main:scripts/check_memory_secrets.py | python3 -",
    )
    if tuple(_read_text_file(hook_path, "pre-commit hook").splitlines()) != expected_lines:
        _fail(GatewayErrorCode.COMMIT_GUARD_REJECTED, "pre-commit hook does not contain the reviewed guards")
    return guard


def _receipt(
    context: GatewayContext,
    state: _CanonicalState,
    *,
    kind: ReceiptKind,
    digest: str,
    head_sha: str | None = None,
    patch_sha256: str | None = None,
) -> ReceiptBinding:
    matches = [
        item
        for item in context.receipts
        if item.kind is kind
        and item.assignment_id == state.assignment["assignment_id"]
        and item.stage_id == state.assignment["stage_id"]
        and item.receipt_sha256 == digest
    ]
    if len(matches) != 1:
        _fail(GatewayErrorCode.RECEIPT_MISMATCH, "trusted receipt binding is absent or ambiguous")
    receipt = matches[0]
    if receipt.decision != RECEIPT_DECISIONS[kind]:
        _fail(GatewayErrorCode.RECEIPT_MISMATCH, "receipt decision is not the required independently validated result")
    if head_sha is not None and receipt.head_sha != head_sha:
        _fail(GatewayErrorCode.RECEIPT_MISMATCH, "receipt reviewed SHA mismatch")
    if patch_sha256 is not None and receipt.patch_sha256 != patch_sha256:
        _fail(GatewayErrorCode.RECEIPT_MISMATCH, "receipt patch digest mismatch")
    receipt_path = _safe_existing_relative(state.root, receipt.path, "receipt")
    expected_file_digest = receipt.file_sha256 or receipt.receipt_sha256
    if _hash_file(receipt_path, "receipt") != expected_file_digest:
        _fail(GatewayErrorCode.RECEIPT_MISMATCH, "receipt file differs from trusted digest")
    return receipt


def _candidate(context: GatewayContext, assignment_id: str, candidate_sha: str) -> CandidateBinding:
    matches = [
        item
        for item in context.candidates
        if item.assignment_id == assignment_id and item.candidate_sha == candidate_sha
    ]
    if len(matches) != 1:
        _fail(GatewayErrorCode.STATE_MISMATCH, "trusted candidate binding is absent or ambiguous")
    return matches[0]


def _base_plan(request: Mapping[str, Any], state: _CanonicalState, **values: Any) -> dict[str, Any]:
    bindings = {
        "assignment": str(state.assignment["assignment_id"]),
        "stage": str(state.assignment["stage_id"]),
        "epoch": str(state.fence["epoch"]),
        "integration_head": str(request["integration_head"]),
        **{key: str(value) for key, value in values.items()},
    }
    return {
        "request_id": str(request["id"]),
        "operation": str(request["operation"]),
        "actor_id": str(request["actor_id"]),
        "role": state.actor.role,
        "bindings": tuple(sorted(bindings.items())),
    }


def _plan_read(request: Mapping[str, Any], state: _CanonicalState) -> OperationPlan:
    operation = str(request["operation"])
    target = _target_for_actor(state)
    if operation == "inspect":
        commands = (
            CommandPlan(
                ("git", "-C", str(target), "rev-parse", "--show-toplevel", "HEAD"),
                str(target),
                EnvClass.READ_ONLY,
                "inspect exact worktree root and HEAD",
            ),
        )
    elif operation == "status":
        commands = (_git_status(target, "inspect exact full Git status"),)
    else:
        revision = "HEAD" if state.actor.role is ActorRole.COORDINATOR else f"{state.assignment['base_sha']}..HEAD"
        commands = (
            CommandPlan(
                ("git", "-C", str(target), "diff", "--no-ext-diff", "--binary", "--full-index", revision, "--"),
                str(target),
                EnvClass.READ_ONLY,
                "inspect exact bounded Git diff",
            ),
        )
    return OperationPlan(
        **_base_plan(request, state, target=target),
        commands=commands,
        followups=(),
        checkpoints=(),
        preconditions=("protected controller must compare command cwd to the mounted exact worktree",),
        postconditions=("operation is read-only; Git HEAD and full status must remain unchanged",),
    )


def _plan_worker_check(request: Mapping[str, Any], state: _CanonicalState) -> OperationPlan:
    return OperationPlan(
        **_base_plan(request, state),
        commands=(_fence_command(state, "worker-check"),),
        followups=(),
        checkpoints=(),
        preconditions=("protected hook bound the actual Worker/Verifier conversation ID",),
        postconditions=("canonical fence command must exit zero; no file may change",),
    )


def _plan_test(request: Mapping[str, Any], state: _CanonicalState) -> OperationPlan:
    selectors = _test_selectors(request["selectors"], state)
    if state.actor.role is ActorRole.VERIFIER:
        if not state.stage_snapshot.clean or state.stage_snapshot.head_sha != state.stage.get("head_sha"):
            _fail(GatewayErrorCode.STATE_MISMATCH, "Verifier tests require the clean registered candidate HEAD")
    commands = (
        _git_status(state.stage_worktree, "capture pre-test full status"),
        _fence_command(state, "worker-check"),
        CommandPlan(
            (sys.executable, "-m", "pytest", "-q", "--disable-warnings", "--maxfail=1", "--", *selectors),
            str(state.stage_worktree),
            EnvClass.HERMETIC_TEST,
            "run explicit Assignment-scoped tests without arbitrary pytest flags",
        ),
        _git_status(state.stage_worktree, "capture post-test full status"),
    )
    return OperationPlan(
        **_base_plan(request, state, selectors=",".join(selectors)),
        commands=commands,
        followups=(),
        checkpoints=(),
        preconditions=(
            "HERMETIC_TEST must clear inherited environment, set APP_ENV_FILE=config/unit-test.env.example and PYTHONDONTWRITEBYTECODE=1",
            "HERMETIC_TEST must deny network and mount source read-only except approved temp/evidence output",
        ),
        postconditions=("pre/post HEAD and full status must be byte-identical; every test must pass",),
    )


def _plan_verifier_submit(request: Mapping[str, Any], state: _CanonicalState) -> OperationPlan:
    candidate = _sha40(request["candidate_sha"], "Verifier candidate")
    test_receipt = _sha256(request["test_receipt_sha256"], "Verifier test receipt")
    selectors = _test_selectors(request["test_selectors"], state)
    decision = request["decision"]
    findings = request["findings"]
    if decision not in {"VERIFIED", "CORRECTIONS_REQUIRED"}:
        _fail(GatewayErrorCode.INVALID_REQUEST, "Verifier decision is unsupported")
    if (not isinstance(findings, list) or len(findings) > 100
            or any(not isinstance(item, str) or not 1 <= len(item.encode()) <= 4096 for item in findings)):
        _fail(GatewayErrorCode.INVALID_REQUEST, "Verifier findings are not a bounded string array")
    if decision == "VERIFIED" and findings:
        _fail(GatewayErrorCode.INVALID_REQUEST, "VERIFIED cannot carry unresolved findings")
    if decision == "CORRECTIONS_REQUIRED" and not findings:
        _fail(GatewayErrorCode.INVALID_REQUEST, "correction decision requires findings")
    if (not state.stage_snapshot.clean or state.stage_snapshot.head_sha != candidate
            or state.stage.get("status") != "COMPLETE_CANDIDATE" or state.stage.get("head_sha") != candidate):
        _fail(GatewayErrorCode.STATE_MISMATCH, "Verifier submission requires the exact clean stage-advanced candidate")
    return OperationPlan(
        **_base_plan(request, state, candidate=candidate, test_receipt=test_receipt,
                     selectors=",".join(selectors), decision=decision),
        commands=(), followups=(), checkpoints=(),
        preconditions=("protected controller must bind the actual Verifier test receipt and Worker candidate",),
        postconditions=("submission records independent evidence only; it never self-approves Stage completion",),
    )
def _request_allowed_paths(request: Mapping[str, Any], state: _CanonicalState) -> tuple[str, ...]:
    value = request["allowed_paths"]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _fail(GatewayErrorCode.INVALID_REQUEST, "allowed_paths must be a JSON string array")
    assignment_allowed, _ = _validate_assignment_paths(state.assignment)
    if tuple(value) != assignment_allowed:
        _fail(GatewayErrorCode.PATH_REJECTED, "request allowed_paths differs from immutable Assignment order/content")
    return tuple(value)


def _plan_worker_commit(request: Mapping[str, Any], state: _CanonicalState, context: GatewayContext) -> OperationPlan:
    guard = _validate_commit_guard(context, state)
    expected_patch = _sha256(request["expected_patch_sha256"], "expected patch")
    test_receipt = _sha256(request["test_receipt_sha256"], "Worker test receipt")
    if state.stage_snapshot.worktree_patch_sha256 != expected_patch:
        _fail(GatewayErrorCode.PATCH_MISMATCH, "trusted worktree patch digest differs from request")
    if state.stage_snapshot.clean:
        _fail(GatewayErrorCode.STATE_MISMATCH, "worker-commit requires a non-empty candidate diff")
    requested_allowed = _request_allowed_paths(request, state)
    assignment_allowed, assignment_forbidden = _validate_assignment_paths(state.assignment)
    changed = _validate_changed_paths(
        state.stage_worktree,
        state.stage_snapshot.changed_paths,
        assignment_allowed,
        assignment_forbidden,
    )
    message = _commit_message(request["message"], str(state.assignment["stage_id"]))
    if state.stage_snapshot.staged_paths:
        _fail(GatewayErrorCode.UNSUPPORTED_SAFE_WORKFLOW, "pre-staged Worker content is unsupported; controller must begin from an unstaged exact snapshot")
    commands = (
        _fence_command(state, "worker-check"),
        CommandPlan(
            ("git", "-C", str(state.stage_worktree), "add", "--", *changed),
            str(state.stage_worktree),
            EnvClass.GIT_MUTATION,
            "stage only controller-observed Assignment-scoped paths",
        ),
        CommandPlan(
            ("git", "-C", str(state.stage_worktree), "diff", "--cached", "--check"),
            str(state.stage_worktree),
            EnvClass.READ_ONLY,
            "run required staged diff check before commit",
        ),
        CommandPlan(
            ("git", "-C", str(state.stage_worktree), "commit", "-m", message),
            str(state.stage_worktree),
            EnvClass.GIT_MUTATION,
            "commit normally with reviewed repository hooks and signing policy preserved",
        ),
        _git_status(state.stage_worktree, "prove clean Worker handoff"),
    )
    return OperationPlan(
        **_base_plan(
            request,
            state,
            patch=expected_patch,
            test_receipt=test_receipt,
            allowed_paths="\n".join(requested_allowed),
            hook_sha256=guard.hook_sha256,
            memory_guard_sha256=guard.main_memory_guard_sha256,
        ),
        commands=commands,
        followups=(),
        checkpoints=(
            GitCheckpoint(
                after_command_index=1,
                worktree=str(state.stage_worktree),
                expected_head_sha=state.stage_snapshot.head_sha,
                expected_staged_paths=changed,
                expected_staged_patch_sha256=expected_patch,
                forbid_unstaged=True,
                forbid_untracked=True,
            ),
        ),
        preconditions=(
            "executor must recompute the canonical staged patch SHA-256 after git add and require it equals expected_patch_sha256",
            "reviewed pre-commit hook must run git diff --cached --check and main:scripts/check_memory_secrets.py",
            "no alternate core.hooksPath; repository signing policy is preserved",
        ),
        postconditions=(
            "resulting commit must be a direct child of the observed Worker HEAD",
            "Worker worktree must be fully clean; Coordinator must separately run stage-advance",
        ),
    )


def _plan_stage_advance(request: Mapping[str, Any], state: _CanonicalState) -> OperationPlan:
    stage_head = _sha40(request["stage_head"], "Stage HEAD")
    worker_receipt = _sha256(request["worker_receipt_sha256"], "Worker candidate receipt")
    if not state.stage_snapshot.clean or state.stage_snapshot.head_sha != stage_head:
        _fail(GatewayErrorCode.STATE_MISMATCH, "stage-advance requires the clean controller-observed Stage HEAD")
    return OperationPlan(
        **_base_plan(request, state, stage_head=stage_head, worker_receipt=worker_receipt),
        commands=(_fence_command(state, "stage-advance", stage_head=stage_head),),
        followups=(),
        checkpoints=(),
        preconditions=("canonical fence command rechecks candidate ancestry, branch, Assignment, expiry and clean status",),
        postconditions=("registry Stage head/status must be atomically ADVANCED/COMPLETE_CANDIDATE",),
    )


def _plan_integrate(request: Mapping[str, Any], state: _CanonicalState, context: GatewayContext) -> OperationPlan:
    guard = _validate_commit_guard(context, state)
    candidate_sha = _sha40(request["candidate_sha"], "candidate SHA")
    expected_patch = _sha256(request["expected_patch_sha256"], "integration patch")
    verifier_digest = _sha256(request["verifier_receipt_sha256"], "Verifier receipt")
    binding = _candidate(context, str(state.assignment["assignment_id"]), candidate_sha)
    if binding.integration_patch_sha256 != expected_patch or binding.verifier_receipt_sha256 != verifier_digest:
        _fail(GatewayErrorCode.PATCH_MISMATCH, "trusted candidate patch/Verifier binding differs from request")
    _receipt(context, state, kind=ReceiptKind.VERIFIER, digest=verifier_digest, head_sha=candidate_sha)
    if state.stage.get("status") != "COMPLETE_CANDIDATE" or state.stage.get("head_sha") != candidate_sha:
        _fail(GatewayErrorCode.STATE_MISMATCH, "integration requires stage-advance of the exact candidate")
    if not state.stage_snapshot.clean or state.stage_snapshot.head_sha != candidate_sha:
        _fail(GatewayErrorCode.STATE_MISMATCH, "integration requires a clean exact candidate worktree")
    if state.stage_snapshot.parent_sha != binding.parent_sha:
        _fail(GatewayErrorCode.STATE_MISMATCH, "candidate parent differs from trusted binding")
    if not state.integration_snapshot.clean:
        _fail(GatewayErrorCode.STATE_MISMATCH, "integration worktree must be fully clean")
    requested_allowed = _request_allowed_paths(request, state)
    allowed, forbidden = _validate_assignment_paths(state.assignment)
    changed = _validate_changed_paths(state.stage_worktree, binding.changed_paths, allowed, forbidden)
    selectors = _test_selectors(request["test_selectors"], state)
    if selectors != binding.test_selectors:
        _fail(GatewayErrorCode.STATE_MISMATCH, "integration tests differ from trusted candidate binding")
    message = _commit_message(request["message"], str(state.assignment["stage_id"]))
    commands = (
        _fence_command(state, "check"),
        CommandPlan(
            ("git", "-C", str(state.root), "cherry-pick", "--no-commit", candidate_sha),
            str(state.root),
            EnvClass.GIT_MUTATION,
            "apply only the exact independently verified candidate commit without committing",
        ),
        CommandPlan(
            ("git", "-C", str(state.root), "diff", "--cached", "--check"),
            str(state.root),
            EnvClass.READ_ONLY,
            "checkpoint integration index syntax/whitespace",
        ),
        CommandPlan(
            (sys.executable, "-m", "pytest", "-q", "--disable-warnings", "--maxfail=1", "--", *selectors),
            str(state.root),
            EnvClass.HERMETIC_TEST,
            "verify exact integration candidate with explicit tests",
        ),
        CommandPlan(
            ("git", "-C", str(state.root), "diff", "--cached", "--binary", "--full-index", "--"),
            str(state.root),
            EnvClass.READ_ONLY,
            "capture exact staged integration patch for controller digest check",
        ),
        CommandPlan(
            ("git", "-C", str(state.root), "commit", "-m", message),
            str(state.root),
            EnvClass.GIT_MUTATION,
            "commit exact integrated patch with reviewed repository hooks/signing",
        ),
    )
    followup = FollowupCommandPlan(
        argv_template=_fence_command(state, "advance", integration_head=RESULTING_HEAD).argv,
        cwd=str(state.root),
        env_class=EnvClass.FENCE,
        observed_value=RESULTING_HEAD,
        purpose="advance canonical fence using only controller-observed resulting integration HEAD",
    )
    return OperationPlan(
        **_base_plan(
            request,
            state,
            candidate=candidate_sha,
            patch=expected_patch,
            allowed_paths="\n".join(requested_allowed),
            changed_paths="\n".join(changed),
            hook_sha256=guard.hook_sha256,
            memory_guard_sha256=guard.main_memory_guard_sha256,
        ),
        commands=commands,
        followups=(followup,),
        checkpoints=(
            GitCheckpoint(
                after_command_index=1,
                worktree=str(state.root),
                expected_head_sha=state.integration_snapshot.head_sha,
                expected_staged_paths=changed,
                expected_staged_patch_sha256=expected_patch,
                forbid_unstaged=True,
                forbid_untracked=True,
            ),
            GitCheckpoint(
                after_command_index=3,
                worktree=str(state.root),
                expected_head_sha=state.integration_snapshot.head_sha,
                expected_staged_paths=changed,
                expected_staged_patch_sha256=expected_patch,
                forbid_unstaged=True,
                forbid_untracked=True,
            ),
        ),
        preconditions=(
            "candidate binding represents exactly one independently VERIFIED correction commit; arbitrary ranges are unsupported",
            "after cherry-pick --no-commit, executor must require no conflicts, exact changed paths, no unstaged/untracked paths and exact expected staged patch digest",
            "HERMETIC_TEST clears inherited environment, denies network and preserves staged/source state",
            "reviewed pre-commit memory-secret guard and repository signing policy remain active",
        ),
        postconditions=(
            "integration commit must be a direct child of request integration_head",
            "followup substitutes only controller-observed resulting HEAD and canonical fence advance must pass",
            "integration worktree must finish fully clean",
        ),
    )


def _review_bundle(request: Mapping[str, Any], state: _CanonicalState) -> Path:
    stage_id = str(state.assignment["stage_id"])
    assignment_id = str(state.assignment["assignment_id"])
    expected = f".local/two-site-refactor/review-bundles/{stage_id}-{assignment_id}"
    if request["bundle"] != expected:
        _fail(GatewayErrorCode.PATH_REJECTED, f"review bundle must be exactly {expected}")
    bundle = _safe_existing_relative(state.root, expected, "review bundle", directory=True)
    for name in REVIEW_BUNDLE_FILES:
        _safe_existing_relative(bundle, name, f"review bundle {name}")
    return bundle


def _review_input_bindings(bundle: Path) -> dict[str, str]:
    return {
        "review_input_" + name.replace(".", "_").lower() + "_sha256":
            _hash_file(bundle / name, "review bundle " + name)
        for name in REVIEW_BUNDLE_FILES
    }


def _bounded_document(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not 1 <= len(value.encode()) <= 30_000
            or "\0" in value or any(ord(character) < 32 and character not in "\n\t\r" for character in value)):
        _fail(GatewayErrorCode.INVALID_REQUEST, label + " is not bounded UTF-8 documentation")
    return value


def _plan_bundle_prepare(request: Mapping[str, Any], state: _CanonicalState,
                         context: GatewayContext) -> OperationPlan:
    base_sha = _sha40(request["base_sha"], "review base")
    head_sha = _sha40(request["head_sha"], "review head")
    if (base_sha != state.assignment["integration_head_at_issue"]
            or head_sha != request["integration_head"] or state.integration_snapshot.head_sha != head_sha):
        _fail(GatewayErrorCode.STATE_MISMATCH, "bundle head differs from exact integration HEAD")
    expected_bundle = f".local/two-site-refactor/review-bundles/{state.assignment['stage_id']}-{state.assignment['assignment_id']}"
    matches = [item for item in context.reviews
               if item.assignment_id == state.assignment["assignment_id"]
               and item.bundle == expected_bundle and item.base_sha == base_sha and item.head_sha == head_sha]
    if len(matches) != 1 or not state.integration_snapshot.clean:
        _fail(GatewayErrorCode.STATE_MISMATCH, "bundle preparation lacks one clean integrated candidate binding")
    stage_report = _bounded_document(request["stage_report"], "Stage report")
    final_request = _bounded_document(request["final_review_request"], "final review request")
    handoff = _bounded_document(request["handoff"], "handoff")
    if base_sha not in final_request or head_sha not in final_request:
        _fail(GatewayErrorCode.INVALID_REQUEST, "final review request must identify exact base and head")
    return OperationPlan(
        **_base_plan(request, state, base=base_sha, head=head_sha, bundle=expected_bundle,
                     stage_report_sha256=hashlib.sha256(stage_report.encode()).hexdigest(),
                     final_request_sha256=hashlib.sha256(final_request.encode()).hexdigest(),
                     handoff_sha256=hashlib.sha256(handoff.encode()).hexdigest()),
        commands=(), followups=(), checkpoints=(),
        preconditions=("protected host writes only the canonical five ignored review-bundle files",),
        postconditions=("bundle bytes are re-read and hashed before review-request may consume them",),
    )


def _plan_review(request: Mapping[str, Any], state: _CanonicalState, context: GatewayContext) -> OperationPlan:
    if context.source_review_authorization != REVIEW_AUTHORIZATION:
        _fail(GatewayErrorCode.EXTERNAL_ACTION_REJECTED, "bounded OpenAI Codex source-review authorization is absent")
    base_sha = _sha40(request["base_sha"], "review base")
    head_sha = _sha40(request["head_sha"], "review head")
    if (base_sha != state.assignment["integration_head_at_issue"]
            or head_sha != request["integration_head"] or state.integration_snapshot.head_sha != head_sha):
        _fail(GatewayErrorCode.STATE_MISMATCH, "review head differs from exact integration HEAD")
    bundle = _review_bundle(request, state)
    expected_bundle = f".local/two-site-refactor/review-bundles/{state.assignment['stage_id']}-{state.assignment['assignment_id']}"
    matches = [
        item
        for item in context.reviews
        if item.assignment_id == state.assignment["assignment_id"]
        and item.bundle == request["bundle"]
        and item.base_sha == base_sha
        and item.head_sha == head_sha
    ]
    if len(matches) != 1 or request["bundle"] != expected_bundle:
        _fail(GatewayErrorCode.STATE_MISMATCH, "exact review range/bundle lacks a unique trusted controller binding")
    if not state.integration_snapshot.clean:
        _fail(GatewayErrorCode.STATE_MISMATCH, "final review requires a fully clean integration worktree")
    bridge = _safe_existing_relative(state.root, "scripts/two_site_codex_final_review.sh", "Codex final-review bridge")
    argv = (
        str(bridge),
        "--stage",
        str(state.assignment["stage_id"]),
        "--assignment",
        str(state.assignment["assignment_id"]),
        "--bundle",
        str(request["bundle"]),
        "--base",
        base_sha,
        "--head",
        head_sha,
    )
    return OperationPlan(
        **_base_plan(request, state, base=base_sha, head=head_sha, bundle=bundle,
                     **_review_input_bindings(bundle)),
        commands=(CommandPlan(argv, str(state.root), EnvClass.EXTERNAL_CODEX_REVIEW, "request canonical exact-SHA Codex final review"),),
        followups=(),
        checkpoints=(),
        preconditions=(
            "only the protected external review supervisor may execute EXTERNAL_CODEX_REVIEW",
            "supervisor must independently prove bridge bytes are tracked at exact clean head before execution",
            "supervisor must enforce the existing scoped source authorization and exclude secrets/unrelated data",
        ),
        postconditions=("receipt remains untrusted until canonical bridge validation and controller digest binding succeed",),
    )


def _plan_closeout_prepare(request: Mapping[str, Any], state: _CanonicalState,
                           context: GatewayContext) -> OperationPlan:
    accepted = _sha40(request["accepted_head"], "accepted Stage head")
    final_digest = _sha256(request["final_receipt_sha256"], "Codex final receipt")
    if (accepted != request["integration_head"] or state.integration_snapshot.head_sha != accepted
            or not state.integration_snapshot.clean):
        _fail(GatewayErrorCode.STATE_MISMATCH, "closeout preparation requires the exact clean accepted HEAD")
    receipt = _receipt(context, state, kind=ReceiptKind.CODEX_FINAL,
                       digest=final_digest, head_sha=accepted)
    return OperationPlan(
        **_base_plan(request, state, accepted_head=accepted, final_receipt=receipt.path,
                     final_receipt_file_sha256=str(receipt.file_sha256 or receipt.receipt_sha256)),
        commands=(), followups=(), checkpoints=(),
        preconditions=("protected host derives all four closeout files from canonical state and accepted receipt",),
        postconditions=("only the exact canonical four-path patch is staged; no commit or approval is implied",),
    )


def _plan_closeout_review(request: Mapping[str, Any], state: _CanonicalState, context: GatewayContext) -> OperationPlan:
    if context.source_review_authorization != REVIEW_AUTHORIZATION:
        _fail(GatewayErrorCode.EXTERNAL_ACTION_REJECTED, "bounded OpenAI Codex source-review authorization is absent")
    accepted = _sha40(request["accepted_head"], "accepted Stage head")
    final_digest = _sha256(request["final_receipt_sha256"], "Codex final receipt")
    if accepted != request["integration_head"] or state.integration_snapshot.head_sha != accepted:
        _fail(GatewayErrorCode.STATE_MISMATCH, "closeout review must target the exact accepted integration HEAD")
    if state.integration_snapshot.unstaged_paths or state.integration_snapshot.untracked_paths:
        _fail(GatewayErrorCode.STATE_MISMATCH, "closeout review forbids unstaged or untracked source")
    if tuple(sorted(state.integration_snapshot.staged_paths)) != _closeout_paths(state):
        _fail(GatewayErrorCode.PATH_REJECTED, "closeout review requires the exact four canonical staged paths")
    patch = state.integration_snapshot.staged_patch_sha256
    if not isinstance(patch, str) or not SHA256.fullmatch(patch):
        _fail(GatewayErrorCode.PATCH_MISMATCH, "closeout staged patch is missing")
    receipt = _receipt(context, state, kind=ReceiptKind.CODEX_FINAL,
                       digest=final_digest, head_sha=accepted)
    bridge = _safe_existing_relative(state.root, "scripts/two_site_codex_closeout_review.sh", "Codex closeout bridge")
    bundle = f".local/two-site-refactor/review-bundles/{state.assignment['stage_id']}-{state.assignment['assignment_id']}"
    bundle_path = _safe_existing_relative(state.root, bundle, "closeout review bundle", directory=True)
    argv = (str(bridge), "--stage", str(state.assignment["stage_id"]), "--assignment",
            str(state.assignment["assignment_id"]), "--bundle", bundle, "--accepted-head", accepted)
    return OperationPlan(
        **_base_plan(request, state, accepted_head=accepted, final_receipt=receipt.path,
                     final_receipt_file_sha256=str(receipt.file_sha256 or receipt.receipt_sha256),
                     closeout_patch=patch, bundle=bundle,
                     **_review_input_bindings(bundle_path)),
        commands=(CommandPlan(argv, str(state.root), EnvClass.EXTERNAL_CODEX_REVIEW,
                              "request canonical exact-patch closeout pre-action review"),),
        followups=(), checkpoints=(),
        preconditions=("protected exact-SHA final receipt is valid and closeout index has exactly four paths",),
        postconditions=("closeout receipt remains untrusted until protected schema/hash binding succeeds",),
    )
def _closeout_paths(state: _CanonicalState) -> tuple[str, ...]:
    stage = str(state.assignment["stage_id"])
    assignment = str(state.assignment["assignment_id"])
    prefix = f"docs/refactor/two-server/reviews/{stage}-{assignment}"
    return tuple(
        sorted(
            (
                "docs/refactor/two-server/CHANGE_LEDGER.md",
                "docs/refactor/two-server/execution-order.yaml",
                f"{prefix}.json",
                f"{prefix}.md",
            )
        )
    )


def _plan_closeout(request: Mapping[str, Any], state: _CanonicalState, context: GatewayContext) -> OperationPlan:
    guard = _validate_commit_guard(context, state)
    accepted_head = _sha40(request["accepted_head"], "accepted Stage head")
    expected_patch = _sha256(request["expected_patch_sha256"], "closeout patch")
    receipt_digest = _sha256(request["closeout_receipt_sha256"], "closeout receipt")
    if accepted_head != request["integration_head"] or state.integration_snapshot.head_sha != accepted_head:
        _fail(GatewayErrorCode.STATE_MISMATCH, "closeout accepted head differs from integration/fence head")
    if state.integration_snapshot.unstaged_paths or state.integration_snapshot.untracked_paths:
        _fail(GatewayErrorCode.STATE_MISMATCH, "closeout forbids unstaged or untracked paths")
    if tuple(sorted(state.integration_snapshot.staged_paths)) != _closeout_paths(state):
        _fail(GatewayErrorCode.PATH_REJECTED, "closeout staged paths are not the exact four canonical paths")
    if state.integration_snapshot.staged_patch_sha256 != expected_patch:
        _fail(GatewayErrorCode.PATCH_MISMATCH, "trusted staged closeout digest differs from request")
    receipt = _receipt(
        context,
        state,
        kind=ReceiptKind.CODEX_CLOSEOUT_PREACTION,
        digest=receipt_digest,
        head_sha=accepted_head,
        patch_sha256=expected_patch,
    )
    message = _commit_message(request["message"], str(state.assignment["stage_id"]))
    command = CommandPlan(
        ("git", "-C", str(state.root), "commit", "-m", message),
        str(state.root),
        EnvClass.GIT_MUTATION,
        "commit exact pre-approved closeout with reviewed hooks/signing",
    )
    bridge = _safe_existing_relative(state.root, "scripts/two_site_codex_closeout_review.sh", "Codex closeout bridge")
    verify_template = (
        str(bridge),
        "--verify-commit",
        "--stage",
        str(state.assignment["stage_id"]),
        "--assignment",
        str(state.assignment["assignment_id"]),
        "--bundle",
        f".local/two-site-refactor/review-bundles/{state.assignment['stage_id']}-{state.assignment['assignment_id']}",
        "--accepted-head",
        accepted_head,
        "--closeout-head",
        RESULTING_HEAD,
    )
    return OperationPlan(
        **_base_plan(
            request,
            state,
            accepted_head=accepted_head,
            patch=expected_patch,
            receipt=receipt.path,
            hook_sha256=guard.hook_sha256,
            memory_guard_sha256=guard.main_memory_guard_sha256,
        ),
        commands=(command,),
        followups=(FollowupCommandPlan(
            verify_template,
            str(state.root),
            EnvClass.READ_ONLY,
            RESULTING_HEAD,
            "verify resulting closeout commit before any fence advance",
        ),),
        checkpoints=(),
        preconditions=(
            "canonical closeout pre-action receipt is controller-validated and bound to exact staged patch",
            "reviewed pre-commit memory-secret guard and repository signing policy remain active",
        ),
        postconditions=(
            "executor substitutes only its observed single-parent resulting HEAD",
            "protected CLOSEOUT_COMMIT authority must be persisted before a separate fence advance",
            "integration worktree must be fully clean",
        ),
    )


def _plan_closeout_advance(request: Mapping[str, Any], state: _CanonicalState,
                           context: GatewayContext) -> OperationPlan:
    closeout_head = _sha40(request["closeout_head"], "closeout head")
    receipt_digest = _sha256(request["closeout_commit_receipt_sha256"], "closeout commit receipt")
    if (not state.integration_snapshot.clean or state.integration_snapshot.head_sha != closeout_head
            or state.integration_snapshot.parent_sha != request["integration_head"]):
        _fail(GatewayErrorCode.STATE_MISMATCH, "closeout advance requires one exact clean child commit")
    receipt = _receipt(context, state, kind=ReceiptKind.CLOSEOUT_COMMIT,
                       digest=receipt_digest, head_sha=closeout_head)
    return OperationPlan(
        **_base_plan(request, state, closeout_head=closeout_head, receipt=receipt.path),
        commands=(_fence_command(state, "advance", integration_head=closeout_head),),
        followups=(), checkpoints=(),
        preconditions=("protected CLOSEOUT_COMMIT=VERIFIED authority exists for this exact child",),
        postconditions=("canonical fence/registry advance succeeds before dependencies are recomputed",),
    )


def _next_stage_metadata(state: _CanonicalState, next_stage: Any, expected_state: str) -> str:
    if not isinstance(next_stage, str) or not STAGE_ID.fullmatch(next_stage):
        _fail(GatewayErrorCode.INVALID_REQUEST, "next Stage ID is invalid")
    order = _load_mapping(
        state.root / "docs/refactor/two-server/execution-order.yaml", "execution order")
    stages = order.get("stages")
    current = stages.get(state.assignment["stage_id"]) if isinstance(stages, dict) else None
    candidate = stages.get(next_stage) if isinstance(stages, dict) else None
    if not isinstance(current, dict) or current.get("state") != "COMPLETE":
        _fail(GatewayErrorCode.STATE_MISMATCH, "current Stage is not COMPLETE")
    if not isinstance(candidate, dict) or candidate.get("state") != expected_state:
        _fail(GatewayErrorCode.STATE_MISMATCH, f"next Stage is not {expected_state}")
    unknown = sorted(set(candidate) - STAGE_METADATA_FIELDS)
    if unknown:
        _fail(GatewayErrorCode.EXTERNAL_ACTION_REJECTED,
              "next Stage has unknown metadata: " + ", ".join(unknown))
    for flag in EXTERNAL_STAGE_FLAGS:
        value = candidate.get(flag, False)
        if not isinstance(value, bool) or value:
            _fail(GatewayErrorCode.EXTERNAL_ACTION_REJECTED,
                  f"automatic next Stage forbids {flag}")
    dependencies = candidate.get("depends_on")
    if (not isinstance(dependencies, list)
            or any(not isinstance(item, str) or not isinstance(stages.get(item), dict)
                   or stages[item].get("state") != "COMPLETE" for item in dependencies)):
        _fail(GatewayErrorCode.STATE_MISMATCH, "next Stage dependencies are not COMPLETE")
    if expected_state == "PROPOSED":
        waves = order.get("waves")
        if (not isinstance(waves, list) or any(not isinstance(wave, dict)
                                               or not isinstance(wave.get("stages"), list)
                                               for wave in waves)):
            _fail(GatewayErrorCode.STATE_MISMATCH, "execution waves are malformed")
        selected = None
        for wave in waves:
            for stage_id in wave["stages"]:
                metadata = stages.get(stage_id) if isinstance(stage_id, str) else None
                if not isinstance(metadata, dict) or metadata.get("state") != "PROPOSED":
                    continue
                if set(metadata) - STAGE_METADATA_FIELDS:
                    _fail(GatewayErrorCode.EXTERNAL_ACTION_REJECTED,
                          f"dependency-ready Stage {stage_id} has unknown metadata")
                flags = [metadata.get(flag, False) for flag in EXTERNAL_STAGE_FLAGS]
                if any(not isinstance(flag, bool) for flag in flags):
                    _fail(GatewayErrorCode.EXTERNAL_ACTION_REJECTED,
                          f"dependency-ready Stage {stage_id} has malformed flags")
                deps = metadata.get("depends_on")
                ready = (isinstance(deps, list) and all(
                    isinstance(dep, str) and isinstance(stages.get(dep), dict)
                    and stages[dep].get("state") == "COMPLETE" for dep in deps))
                if ready and not any(flags):
                    selected = stage_id
                    break
            if selected is not None:
                break
        if selected != next_stage:
            _fail(GatewayErrorCode.STATE_MISMATCH,
                  f"next Stage is not the deterministic first safe dependency-ready Stage: {selected}")
    return next_stage


def _plan_next_stage_prepare(request: Mapping[str, Any], state: _CanonicalState,
                             context: GatewayContext) -> OperationPlan:
    next_stage = _next_stage_metadata(state, request["next_stage"], "PROPOSED")
    receipt_sha = _sha256(request["closeout_commit_receipt_sha256"], "closeout commit receipt")
    receipt = _receipt(context, state, kind=ReceiptKind.CLOSEOUT_COMMIT,
                       digest=receipt_sha, head_sha=request["integration_head"])
    if not state.integration_snapshot.clean:
        _fail(GatewayErrorCode.STATE_MISMATCH, "next Stage preparation requires a clean integration worktree")
    return OperationPlan(
        **_base_plan(request, state, next_stage=next_stage, closeout_receipt=receipt.path),
        commands=(_fence_command(state, "stage-open-check", next_stage=next_stage),),
        followups=(), checkpoints=(),
        preconditions=("verified closeout commit exists and canonical dependencies are COMPLETE",),
        postconditions=("protected host stages only the deterministic PROPOSED-to-IN_PROGRESS line",),
    )


def _plan_next_stage_commit(request: Mapping[str, Any], state: _CanonicalState,
                            context: GatewayContext) -> OperationPlan:
    guard = _validate_commit_guard(context, state)
    next_stage = _next_stage_metadata(state, request["next_stage"], "IN_PROGRESS")
    patch = _sha256(request["expected_patch_sha256"], "next Stage transition patch")
    prepare_sha = _sha256(request["prepare_receipt_sha256"], "next Stage prepare receipt")
    receipt = _receipt(context, state, kind=ReceiptKind.NEXT_STAGE_PREPARE,
                       digest=prepare_sha, head_sha=request["integration_head"], patch_sha256=patch)
    expected_path = ("docs/refactor/two-server/execution-order.yaml",)
    if (state.integration_snapshot.staged_paths != expected_path
            or state.integration_snapshot.unstaged_paths or state.integration_snapshot.untracked_paths
            or state.integration_snapshot.staged_patch_sha256 != patch):
        _fail(GatewayErrorCode.PATCH_MISMATCH, "next Stage transition is not the exact prepared one-file patch")
    message = _commit_message(request["message"], next_stage)
    command = CommandPlan(
        ("git", "-C", str(state.root), "commit", "-m", message), str(state.root),
        EnvClass.GIT_MUTATION, "commit deterministic next Stage state transition",
    )
    followup = FollowupCommandPlan(
        _fence_command(state, "advance", integration_head=RESULTING_HEAD).argv,
        str(state.root), EnvClass.FENCE, RESULTING_HEAD,
        "advance fence to the observed next Stage transition commit",
    )
    return OperationPlan(
        **_base_plan(request, state, next_stage=next_stage, patch=patch,
                     prepare_receipt=receipt.path, hook_sha256=guard.hook_sha256,
                     memory_guard_sha256=guard.main_memory_guard_sha256),
        commands=(command,), followups=(followup,), checkpoints=(),
        preconditions=("one protected prepare authority binds the exact one-file staged patch",),
        postconditions=("commit is a clean direct child and fence advances to its observed HEAD",),
    )


NEXT_CONTRACT_KEYS = {
    "slug", "allowed_paths", "forbidden_paths", "locks", "deliverables",
    "tests", "failure_tests", "rollback_test", "scope_notes",
}
PROTECTED_ASSIGNMENT_PATTERNS = {
    ".agents/**",
    "docs/refactor/two-server/CHANGE_LEDGER.md",
    "docs/refactor/two-server/execution-order.yaml",
    "docs/refactor/two-server/reviews/**",
    "scripts/two_site_agent_*.py",
}


def _next_assignment_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != NEXT_CONTRACT_KEYS:
        _fail(GatewayErrorCode.INVALID_REQUEST, "next Assignment contract keys differ from schema")
    slug = value.get("slug")
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", slug):
        _fail(GatewayErrorCode.INVALID_REQUEST, "next Assignment slug is invalid")
    rollback = value.get("rollback_test")
    if rollback is not None and (not isinstance(rollback, str) or not 1 <= len(rollback.encode()) <= 4096):
        _fail(GatewayErrorCode.INVALID_REQUEST, "next Assignment rollback test is invalid")
    for key in ("allowed_paths", "forbidden_paths", "locks", "deliverables", "tests", "failure_tests"):
        items = value.get(key)
        if (not isinstance(items, list) or (key == "allowed_paths" and not items)
                or len(items) > 256 or len(items) != len(set(items))
                or any(not isinstance(item, str) or not 1 <= len(item.encode()) <= 4096
                       or "\0" in item for item in items)):
            _fail(GatewayErrorCode.INVALID_REQUEST, f"next Assignment {key} is invalid")
    scope_notes = value.get("scope_notes")
    if not isinstance(scope_notes, str) or not 1 <= len(scope_notes.encode()) <= 20_000:
        _fail(GatewayErrorCode.INVALID_REQUEST, "next Assignment scope notes are invalid")
    paths = {"allowed_paths": value["allowed_paths"], "forbidden_paths": value["forbidden_paths"]}
    _validate_assignment_paths(paths)
    if not PROTECTED_ASSIGNMENT_PATTERNS.issubset(set(value["forbidden_paths"])):
        _fail(GatewayErrorCode.PATH_REJECTED, "next Assignment omits mandatory protected paths")
    return value


def _plan_next_stage_bind(request: Mapping[str, Any], state: _CanonicalState,
                          context: GatewayContext) -> OperationPlan:
    next_stage = _next_stage_metadata(state, request["next_stage"], "IN_PROGRESS")
    next_assignment = request["next_assignment"]
    if not isinstance(next_assignment, str) or not ASSIGNMENT_ID.fullmatch(next_assignment):
        _fail(GatewayErrorCode.INVALID_REQUEST, "next Assignment ID is invalid")
    if not next_assignment.startswith("WA-" + next_stage.upper() + "-"):
        _fail(GatewayErrorCode.INVALID_REQUEST, "next Assignment ID is not namespaced to its Stage")
    if next_assignment == state.assignment["assignment_id"]:
        _fail(GatewayErrorCode.INVALID_REQUEST, "next Assignment must be new")
    contract = _next_assignment_contract(request["contract"])
    authority = _sha256(request["next_stage_commit_receipt_sha256"], "next Stage commit receipt")
    receipt = _receipt(context, state, kind=ReceiptKind.NEXT_STAGE_COMMIT,
                       digest=authority, head_sha=request["integration_head"])
    if not state.integration_snapshot.clean:
        _fail(GatewayErrorCode.STATE_MISMATCH, "Stage bind requires a clean integration worktree")
    command = _fence_command(state, "stage-bind", next_assignment=next_assignment)
    return OperationPlan(
        **_base_plan(request, state, next_stage=next_stage, next_assignment=next_assignment,
                     slug=contract["slug"], contract_sha256=hashlib.sha256(
                         json.dumps(contract, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest(),
                     next_stage_commit_receipt=receipt.path),
        commands=(command,), followups=(), checkpoints=(),
        preconditions=("protected host creates only the validated Assignment/registered worktree at exact HEAD",),
        postconditions=("stage-bind atomically closes prior registry record and activates the new Assignment",),
    )


def _plan_receipt_check(request: Mapping[str, Any], state: _CanonicalState, context: GatewayContext) -> OperationPlan:
    try:
        kind = ReceiptKind(request["receipt_kind"])
    except (ValueError, TypeError) as exc:
        raise GatewayPlanError(GatewayErrorCode.INVALID_REQUEST, "receipt_kind is unsupported") from exc
    digest = _sha256(request["receipt_sha256"], "receipt")
    reviewed_sha = _sha40(request["reviewed_sha"], "reviewed SHA")
    receipt = _receipt(context, state, kind=kind, digest=digest, head_sha=reviewed_sha)
    return OperationPlan(
        **_base_plan(request, state, receipt_kind=kind.value, receipt=receipt.path, reviewed_sha=reviewed_sha),
        commands=(),
        followups=(),
        checkpoints=(),
        preconditions=("protected controller already ran the canonical receipt validator; planner checks identity and file digest only",),
        postconditions=("NO_EXECUTION: this operation never creates VERIFIED or APPROVE authority",),
    )


def plan_operation(request: Mapping[str, object], context: GatewayContext) -> OperationPlan:
    """Validate one exact gateway request and return an immutable execution plan.

    The function reads canonical files but never executes commands, changes Git, writes
    receipts or mutates registry/fence state.  The protected caller identity comes only
    from ``context.caller_id`` and ``context.actors``; a role is never accepted from the
    request.
    """

    if not isinstance(request, dict) or any(not isinstance(key, str) for key in request):
        _fail(GatewayErrorCode.INVALID_REQUEST, "request must be a JSON object with string keys")
    operation = request.get("operation")
    if not isinstance(operation, str) or operation not in OPERATION_KEYS:
        _fail(GatewayErrorCode.UNKNOWN_OPERATION, "operation is not in the fixed gateway catalog")
    expected_keys = COMMON_KEYS | OPERATION_KEYS[operation]
    if set(request) != expected_keys:
        missing = sorted(expected_keys - set(request))
        unknown = sorted(set(request) - expected_keys)
        _fail(
            GatewayErrorCode.INVALID_REQUEST,
            f"request keys differ from {operation} schema; missing={missing}, unknown={unknown}",
        )
    _canonical_uuid(request["id"], "request ID")
    _canonical_uuid(request["actor_id"], "actor ID")
    assignment = request["assignment"]
    if not isinstance(assignment, str) or not ASSIGNMENT_ID.fullmatch(assignment):
        _fail(GatewayErrorCode.INVALID_REQUEST, "assignment ID is invalid")
    _integer(request["epoch"], "epoch")
    _sha40(request["integration_head"], "integration head")

    state = _load_state(request, context)
    if operation not in ROLE_OPERATIONS[state.actor.role]:
        _fail(GatewayErrorCode.ROLE_FORBIDDEN, f"{state.actor.role.value} cannot perform {operation}")

    if operation in {"inspect", "status", "diff"}:
        return _plan_read(request, state)
    if operation == "worker-check":
        return _plan_worker_check(request, state)
    if operation == "test":
        return _plan_test(request, state)
    if operation == "verifier-submit":
        return _plan_verifier_submit(request, state)
    if operation == "worker-commit":
        return _plan_worker_commit(request, state, context)
    if operation == "stage-advance":
        return _plan_stage_advance(request, state)
    if operation == "integrate":
        return _plan_integrate(request, state, context)
    if operation == "bundle-prepare":
        return _plan_bundle_prepare(request, state, context)
    if operation == "review-request":
        return _plan_review(request, state, context)
    if operation == "closeout-prepare":
        return _plan_closeout_prepare(request, state, context)
    if operation == "closeout-review":
        return _plan_closeout_review(request, state, context)
    if operation == "closeout-commit":
        return _plan_closeout(request, state, context)
    if operation == "closeout-advance":
        return _plan_closeout_advance(request, state, context)
    if operation == "next-stage-prepare":
        return _plan_next_stage_prepare(request, state, context)
    if operation == "next-stage-commit":
        return _plan_next_stage_commit(request, state, context)
    if operation == "next-stage-bind":
        return _plan_next_stage_bind(request, state, context)
    if operation == "receipt-check":
        return _plan_receipt_check(request, state, context)
    _fail(GatewayErrorCode.UNKNOWN_OPERATION, "operation dispatch is incomplete")


__all__ = [
    "ActorBinding",
    "ActorRole",
    "CandidateBinding",
    "CommandPlan",
    "CommitGuard",
    "EnvClass",
    "FollowupCommandPlan",
    "GatewayContext",
    "GatewayErrorCode",
    "GatewayPlanError",
    "GitSnapshot",
    "GitCheckpoint",
    "OperationPlan",
    "ReceiptBinding",
    "ReceiptKind",
    "ReviewBinding",
    "RESULTING_HEAD",
    "plan_operation",
]
