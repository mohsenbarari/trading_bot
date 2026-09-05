#!/usr/bin/env python3
"""Fail-closed executor for protected two-site Agent operation plans.

The Agent never supplies a command to this module.  A protected controller first
constructs an ``OperationPlan`` with ``two_site_agent_gateway`` and pins every
executable/script/worktree used below.  This executor independently checks the fixed
per-operation command grammar, observes real Git state around every step, and invokes
hermetic tests through ``two_site_agent_sandbox``.

It deliberately has no rollback primitive.  A failed mutation remains visible for
canonical recovery; it is never reset, discarded, or described as successful.
The first version also rejects merge-commit boundaries and rename/copy changes rather
than representing either incompletely.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Protocol, Sequence

try:  # Direct script/controller loading.
    from two_site_agent_gateway import (
        ActorRole,
        CommandPlan,
        EnvClass,
        FollowupCommandPlan,
        GitCheckpoint,
        GitSnapshot,
        OperationPlan,
        RESULTING_HEAD,
    )
    from two_site_agent_sandbox import GitAncestryEvidence, _run_bounded_process, run_tests
except ModuleNotFoundError as error:  # Package-style unit-test import.
    if error.name not in {"two_site_agent_gateway", "two_site_agent_sandbox"}:
        raise
    from scripts.two_site_agent_gateway import (
        ActorRole,
        CommandPlan,
        EnvClass,
        FollowupCommandPlan,
        GitCheckpoint,
        GitSnapshot,
        OperationPlan,
        RESULTING_HEAD,
    )
    from scripts.two_site_agent_sandbox import GitAncestryEvidence, _run_bounded_process, run_tests


GIT_BINARY = Path("/usr/bin/git")
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
ASSIGNMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
STAGE = re.compile(r"[A-Z][A-Z0-9]*-[0-9]+[a-z]?\Z")
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
TEST_SELECTOR = re.compile(r"tests/test[A-Za-z0-9_./-]*\.py(?:::[A-Za-z0-9_]+)*\Z")
MAX_COMMAND_OUTPUT = 2_000_000
MAX_EVIDENCE_TEXT = 64_000
MAX_PLAN_COMMANDS = 8
READ_TIMEOUT = 60
MUTATION_TIMEOUT = 300
FENCE_TIMEOUT = 120
TEST_TIMEOUT = 1200
OPERATION_ROLES = {
    "inspect": {ActorRole.COORDINATOR, ActorRole.WORKER, ActorRole.VERIFIER},
    "status": {ActorRole.COORDINATOR, ActorRole.WORKER, ActorRole.VERIFIER},
    "diff": {ActorRole.COORDINATOR, ActorRole.WORKER, ActorRole.VERIFIER},
    "worker-check": {ActorRole.WORKER, ActorRole.VERIFIER},
    "test": {ActorRole.WORKER, ActorRole.VERIFIER},
    "verifier-submit": {ActorRole.VERIFIER},
    "worker-commit": {ActorRole.WORKER},
    "stage-advance": {ActorRole.COORDINATOR},
    "integrate": {ActorRole.COORDINATOR},
    "bundle-prepare": {ActorRole.COORDINATOR},
    "review-request": {ActorRole.COORDINATOR},
    "closeout-prepare": {ActorRole.COORDINATOR},
    "closeout-review": {ActorRole.COORDINATOR},
    "closeout-commit": {ActorRole.COORDINATOR},
    "closeout-advance": {ActorRole.COORDINATOR},
    "next-stage-prepare": {ActorRole.COORDINATOR},
    "next-stage-commit": {ActorRole.COORDINATOR},
    "next-stage-bind": {ActorRole.COORDINATOR},
    "receipt-check": {ActorRole.COORDINATOR, ActorRole.VERIFIER},
}


class ExecutorError(ValueError):
    """A plan, pin, observation, or checkpoint failed closed."""


@dataclass(frozen=True)
class PinnedPaths:
    """Protected-controller path and byte identity supplied outside Agent input."""

    root: Path
    stage_worktree: Path
    integration_branch: str
    stage_branch: str
    git_binary: Path
    git_sha256: str
    python_binary: Path
    python_sha256: str
    fence_script: Path
    fence_sha256: str
    final_review_bridge: Path
    final_review_sha256: str
    closeout_review_bridge: Path
    closeout_review_sha256: str


@dataclass(frozen=True)
class MutationBoundary:
    """Sanitized state delivered to the protected policy immediately before mutation."""

    request_id: str
    operation: str
    phase: str
    command_index: int
    command: CommandPlan | FollowupCommandPlan
    integration: GitSnapshot
    stage: GitSnapshot


class OperationRunner(Protocol):
    def run_command(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout: int,
        output_limit: int,
    ) -> subprocess.CompletedProcess[bytes]: ...

    def run_hermetic(
        self,
        worktree: Path,
        selectors: Sequence[str],
        *,
        expected_head: str,
        extra_paths: Sequence[str],
        timeout: int,
    ) -> Mapping[str, Any]: ...


class ReviewSupervisor(Protocol):
    def __call__(
        self,
        command: CommandPlan,
        request: Mapping[str, Any],
        bindings: Mapping[str, str],
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class LocalOperationRunner:
    """Reviewed local runner; ancestry is controller-selected, never request-selected."""

    ancestry: tuple[tuple[Path, GitAncestryEvidence], ...] = ()

    def run_command(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout: int,
        output_limit: int,
    ) -> subprocess.CompletedProcess[bytes]:
        return _run_bounded_process(
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            output_limit=output_limit,
        )

    def run_hermetic(
        self,
        worktree: Path,
        selectors: Sequence[str],
        *,
        expected_head: str,
        extra_paths: Sequence[str],
        timeout: int,
    ) -> Mapping[str, Any]:
        matches = [evidence for path, evidence in self.ancestry if path == worktree]
        if len(matches) > 1:
            raise ExecutorError("hermetic ancestry binding is ambiguous")
        evidence = matches[0] if matches else None
        if evidence is not None and evidence.head_sha != expected_head:
            raise ExecutorError("hermetic ancestry head differs from observed HEAD")
        return run_tests(
            worktree,
            list(selectors),
            extra_paths=list(extra_paths),
            timeout=timeout,
            git_ancestry=evidence,
        )


BeforeMutation = Callable[[MutationBoundary], None]


def _safe_env(*, mutation: bool = False) -> dict[str, str]:
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    # Git commit may legitimately require the repository's configured signing agent.
    # No provider/API credential variables are inherited.
    for key in ("HOME", "XDG_CONFIG_HOME", "GNUPGHOME", "SSH_AUTH_SOCK", "GPG_TTY"):
        value = os.environ.get(key)
        if mutation and value:
            env[key] = value
    return env


def _bounded_git(
    worktree: Path,
    arguments: Sequence[str],
    *,
    env_extra: Mapping[str, str] | None = None,
    allowed_exit_codes: tuple[int, ...] = (0,),
    output_limit: int = MAX_COMMAND_OUTPUT,
    mutation_config: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    env = _safe_env(mutation=mutation_config)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    if env_extra:
        env.update(env_extra)
    result = _run_bounded_process(
        [str(GIT_BINARY), "--no-replace-objects", "-C", str(worktree), *arguments],
        cwd=worktree,
        env=env,
        timeout=READ_TIMEOUT,
        output_limit=output_limit,
    )
    if result.returncode not in allowed_exit_codes:
        raise ExecutorError(f"trusted Git observation failed: {arguments[0] if arguments else 'git'}")
    return result


def _decode(value: bytes, label: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExecutorError(f"{label} is not UTF-8") from exc


def _safe_relative(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\\" in value:
        raise ExecutorError(f"{label} is not a normalized relative path")
    if any(ord(character) < 32 for character in value):
        raise ExecutorError(f"{label} contains control characters")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(part in {"", ".", ".."} for part in path.parts):
        raise ExecutorError(f"{label} escapes its worktree")
    if ".git" in path.parts:
        raise ExecutorError(f"{label} addresses Git metadata")
    return value


def _canonical_directory(path: Path, label: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ExecutorError(f"{label} must be an absolute Path")
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ExecutorError(f"{label} is missing") from exc
    if resolved != path or not path.is_dir():
        raise ExecutorError(f"{label} must be a canonical directory without symlinks")
    return path


def _canonical_file(path: Path, label: str, *, executable: bool = False) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ExecutorError(f"{label} must be an absolute Path")
    try:
        mode = path.lstat().st_mode
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ExecutorError(f"{label} is missing") from exc
    if resolved != path or stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ExecutorError(f"{label} must be a canonical regular file")
    if executable and not os.access(path, os.X_OK):
        raise ExecutorError(f"{label} is not executable")
    return path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _index_identity(worktree: Path) -> tuple[str, int, str]:
    raw = _decode(
        _bounded_git(worktree, ("rev-parse", "--path-format=absolute", "--git-path", "index")).stdout,
        "Git index path",
    ).strip()
    path = Path(raw)
    if not path.is_absolute():
        raise ExecutorError("Git returned a non-absolute index path")
    if not path.exists():
        return (str(path), -1, "ABSENT")
    _canonical_file(path, "Git index")
    return (str(path), path.stat().st_size, _file_sha256(path))


def _git_layout(worktree: Path) -> tuple[Path, Path]:
    root = Path(
        _decode(_bounded_git(worktree, ("rev-parse", "--show-toplevel")).stdout, "Git root").strip()
    )
    common = Path(
        _decode(
            _bounded_git(worktree, ("rev-parse", "--path-format=absolute", "--git-common-dir")).stdout,
            "Git common directory",
        ).strip()
    )
    return _canonical_directory(root, "Git worktree root"), _canonical_directory(common, "Git common directory")


def _parse_status(raw: bytes) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    staged: set[str] = set()
    unstaged: set[str] = set()
    untracked: set[str] = set()
    index = 0
    while index < len(fields):
        text = _decode(fields[index], "Git status entry")
        index += 1
        if len(text) < 4 or text[2] != " ":
            raise ExecutorError("Git status entry has an unsupported shape")
        code, path = text[:2], _safe_relative(text[3:], "Git status path")
        if code == "??":
            untracked.add(path)
            continue
        if code == "!!" or "U" in code or code in {"AA", "DD"}:
            raise ExecutorError("ignored/unmerged Git status is unsupported")
        if "R" in code or "C" in code:
            raise ExecutorError("rename/copy Git status is unsupported by the first executor")
        if code[0] not in " MADT" or code[1] not in " MADT":
            raise ExecutorError("Git status code is unsupported")
        if code[0] != " ":
            staged.add(path)
        if code[1] != " ":
            unstaged.add(path)
    return tuple(sorted(staged)), tuple(sorted(unstaged)), tuple(sorted(untracked))


def _canonical_patch(worktree: Path, *, alternate: bool) -> bytes:
    arguments = ("diff", "--cached", "--binary", "--full-index", "--no-ext-diff", "--no-textconv", "--no-renames", "HEAD", "--")
    if not alternate:
        return _bounded_git(worktree, arguments).stdout

    filters = _bounded_git(
        worktree,
        ("config", "--null", "--get-regexp", r"^filter\..*\.(clean|process|required)$"),
        allowed_exit_codes=(0, 1),
        output_limit=64_000,
    )
    if filters.returncode == 0 and filters.stdout:
        raise ExecutorError("configured Git clean/process filters are unsupported for read-only observation")
    fsmonitor = _bounded_git(
        worktree,
        ("config", "--null", "--get", "core.fsmonitor"),
        allowed_exit_codes=(0, 1),
        output_limit=4096,
    )
    if fsmonitor.returncode == 0 and fsmonitor.stdout.strip() not in {b"", b"false\0", b"false"}:
        raise ExecutorError("configured Git fsmonitor is unsupported for read-only observation")
    index_before = _index_identity(worktree)
    _, common = _git_layout(worktree)
    real_objects = _canonical_directory(common / "objects", "Git object directory")
    with tempfile.TemporaryDirectory(prefix="two-site-git-observe-") as temporary:
        temporary_path = Path(temporary)
        object_directory = temporary_path / "objects"
        object_directory.mkdir()
        alternate_index = temporary_path / "index"
        extra = {
            "GIT_INDEX_FILE": str(alternate_index),
            "GIT_OBJECT_DIRECTORY": str(object_directory),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(real_objects),
        }
        _bounded_git(worktree, ("read-tree", "HEAD"), env_extra=extra)
        _bounded_git(worktree, ("add", "-A", "--", "."), env_extra=extra)
        patch = _bounded_git(worktree, arguments, env_extra=extra).stdout
    if _index_identity(worktree) != index_before:
        raise ExecutorError("alternate-index observation changed the real Git index")
    return patch


def _snapshot_once(worktree: Path) -> GitSnapshot:
    root, _ = _git_layout(worktree)
    if root != worktree:
        raise ExecutorError("requested worktree differs from Git toplevel")
    head = _decode(_bounded_git(worktree, ("rev-parse", "--verify", "HEAD^{commit}"), output_limit=128).stdout, "HEAD").strip()
    if not SHA40.fullmatch(head):
        raise ExecutorError("Git HEAD is not a full lowercase SHA")
    branch = _decode(_bounded_git(worktree, ("symbolic-ref", "--quiet", "--short", "HEAD"), output_limit=4096).stdout, "Git branch").strip()
    if not BRANCH.fullmatch(branch):
        raise ExecutorError("detached or malformed Git branch is unsupported")
    parents = _decode(_bounded_git(worktree, ("rev-list", "--parents", "-n", "1", head), output_limit=256).stdout, "Git parents").split()
    if not parents or parents[0] != head or len(parents) > 2:
        raise ExecutorError("merge commits are unsupported at executor boundaries")
    parent = parents[1] if len(parents) == 2 else None
    status = _bounded_git(
        worktree,
        ("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"),
    ).stdout
    staged, unstaged, untracked = _parse_status(status)
    staged_patch = _canonical_patch(worktree, alternate=False)
    worktree_patch = _canonical_patch(worktree, alternate=True)
    return GitSnapshot(
        worktree=worktree,
        branch=branch,
        head_sha=head,
        parent_sha=parent,
        staged_paths=staged,
        unstaged_paths=unstaged,
        untracked_paths=untracked,
        worktree_patch_sha256=hashlib.sha256(worktree_patch).hexdigest() if worktree_patch else None,
        staged_patch_sha256=hashlib.sha256(staged_patch).hexdigest() if staged_patch else None,
    )


def observe_git(worktree: Path) -> GitSnapshot:
    """Return a stable full Git snapshot without changing the real index/object store."""

    worktree = _canonical_directory(worktree, "worktree")
    first = _snapshot_once(worktree)
    second = _snapshot_once(worktree)
    if first != second:
        raise ExecutorError("Git state changed during observation")
    return first


def _validate_pins(pins: PinnedPaths) -> None:
    if not isinstance(pins, PinnedPaths):
        raise ExecutorError("pinned_paths must be a PinnedPaths instance")
    _canonical_directory(pins.root, "integration worktree")
    _canonical_directory(pins.stage_worktree, "Stage worktree")
    if pins.root == pins.stage_worktree:
        raise ExecutorError("integration and Stage worktrees must be distinct")
    if not BRANCH.fullmatch(pins.integration_branch) or not BRANCH.fullmatch(pins.stage_branch):
        raise ExecutorError("pinned Git branch is malformed")
    if pins.git_binary != GIT_BINARY:
        raise ExecutorError("executor supports only the canonical /usr/bin/git")
    expected_paths = {
        "Git binary": (pins.git_binary, pins.git_sha256, True),
        "Python binary": (pins.python_binary, pins.python_sha256, True),
        "fence script": (pins.fence_script, pins.fence_sha256, False),
        "final-review bridge": (pins.final_review_bridge, pins.final_review_sha256, True),
        "closeout bridge": (pins.closeout_review_bridge, pins.closeout_review_sha256, True),
    }
    canonical_scripts = {
        "fence script": pins.root / "scripts" / "two_site_orchestrator_fence.py",
        "final-review bridge": pins.root / "scripts" / "two_site_codex_final_review.sh",
        "closeout bridge": pins.root / "scripts" / "two_site_codex_closeout_review.sh",
    }
    for label, (path, digest, executable) in expected_paths.items():
        _canonical_file(path, label, executable=executable)
        if label in canonical_scripts and path != canonical_scripts[label]:
            raise ExecutorError(f"{label} is not at its pinned project path")
        if not SHA256.fullmatch(digest) or _file_sha256(path) != digest:
            raise ExecutorError(f"{label} digest differs from protected pin")


def _bindings(plan: OperationPlan, request: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(plan.bindings, tuple) or any(
        not isinstance(item, tuple)
        or len(item) != 2
        or not all(isinstance(value, str) for value in item)
        for item in plan.bindings
    ):
        raise ExecutorError("plan bindings are malformed")
    values = dict(plan.bindings)
    if len(values) != len(plan.bindings):
        raise ExecutorError("plan bindings contain duplicate keys")
    required = {
        "assignment": request.get("assignment"),
        "epoch": str(request.get("epoch")),
        "integration_head": request.get("integration_head"),
    }
    if any(values.get(key) != value for key, value in required.items()):
        raise ExecutorError("plan bindings differ from request identity")
    if not STAGE.fullmatch(values.get("stage", "")):
        raise ExecutorError("plan Stage binding is malformed")
    return values


def _validate_command(command: CommandPlan, pins: PinnedPaths) -> None:
    if not isinstance(command, CommandPlan) or not isinstance(command.argv, tuple) or not command.argv:
        raise ExecutorError("plan contains a malformed command")
    if any(not isinstance(value, str) or not value or "\0" in value for value in command.argv):
        raise ExecutorError("command argv contains an invalid value")
    if command.cwd not in {str(pins.root), str(pins.stage_worktree)}:
        raise ExecutorError("command cwd is not a pinned worktree")
    if not isinstance(command.env_class, EnvClass) or command.expected_exit_codes != (0,):
        raise ExecutorError("command environment/exit contract is unsupported")
    if not isinstance(command.purpose, str) or not command.purpose:
        raise ExecutorError("command purpose is missing")


def _validate_followup(command: FollowupCommandPlan, pins: PinnedPaths) -> None:
    if (
        not isinstance(command, FollowupCommandPlan)
        or not isinstance(command.argv_template, tuple)
        or not command.argv_template
        or any(not isinstance(value, str) or not value or "\0" in value for value in command.argv_template)
    ):
        raise ExecutorError("plan contains a malformed followup command")
    if command.cwd not in {str(pins.root), str(pins.stage_worktree)}:
        raise ExecutorError("followup cwd is not a pinned worktree")
    if not isinstance(command.env_class, EnvClass):
        raise ExecutorError("followup environment class is invalid")
    if not isinstance(command.observed_value, str) or not isinstance(command.purpose, str) or not command.purpose:
        raise ExecutorError("followup observed-value/purpose contract is malformed")


def _status_command(command: CommandPlan, cwd: Path) -> bool:
    return command.env_class is EnvClass.READ_ONLY and command.cwd == str(cwd) and command.argv == (
        "git", "-C", str(cwd), "status", "--porcelain=v1", "--untracked-files=all"
    )


def _git_command(command: CommandPlan, cwd: Path, tail: Sequence[str], env: EnvClass) -> bool:
    return command.env_class is env and command.cwd == str(cwd) and command.argv == (
        "git", "-C", str(cwd), *tail
    )


def _parse_fence(
    command: CommandPlan | FollowupCommandPlan,
    pins: PinnedPaths,
    request: Mapping[str, Any],
    *,
    expected_subcommand: str,
    expected_head: str,
    expected_stage_head: str | None = None,
    expected_extra: tuple[str, ...] = (),
) -> str:
    expected_class = EnvClass.FENCE
    if command.env_class is not expected_class or command.cwd != str(pins.root):
        raise ExecutorError("fence command environment/cwd differs from pin")
    argv = command.argv if isinstance(command, CommandPlan) else command.argv_template
    prefix = (
        sys.executable,
        str(pins.fence_script),
        expected_subcommand,
        "--platform", "ANTIGRAVITY",
        "--instance",
    )
    if argv[: len(prefix)] != prefix or len(argv) < len(prefix) + 7:
        raise ExecutorError("fence command prefix is not allowlisted")
    instance = argv[len(prefix)]
    if not UUID4.fullmatch(instance):
        raise ExecutorError("fence instance is not a canonical UUIDv4")
    suffix = (
        "--epoch", str(request["epoch"]),
        "--integration-head", expected_head,
        "--assignment", str(request["assignment"]),
    )
    expected = prefix + (instance,) + suffix
    if expected_stage_head is not None:
        expected += ("--stage-head", expected_stage_head)
    expected += expected_extra
    if argv != expected:
        raise ExecutorError("fence command arguments differ from the fixed contract")
    return instance


def _test_command(command: CommandPlan, cwd: Path, selectors: Sequence[str]) -> bool:
    return command.env_class is EnvClass.HERMETIC_TEST and command.cwd == str(cwd) and command.argv == (
        sys.executable,
        "-m", "pytest", "-q", "--disable-warnings", "--maxfail=1", "--", *selectors,
    )


def _validate_checkpoint(checkpoint: GitCheckpoint, pins: PinnedPaths, command_count: int) -> None:
    if not isinstance(checkpoint, GitCheckpoint):
        raise ExecutorError("checkpoint is not a GitCheckpoint")
    if type(checkpoint.after_command_index) is not int or not 0 <= checkpoint.after_command_index < command_count:
        raise ExecutorError("checkpoint command index is invalid")
    if checkpoint.worktree not in {str(pins.root), str(pins.stage_worktree)}:
        raise ExecutorError("checkpoint worktree is not pinned")
    if not SHA40.fullmatch(checkpoint.expected_head_sha):
        raise ExecutorError("checkpoint HEAD is malformed")
    if (
        not isinstance(checkpoint.expected_staged_paths, tuple)
        or tuple(sorted(set(checkpoint.expected_staged_paths))) != checkpoint.expected_staged_paths
    ):
        raise ExecutorError("checkpoint staged paths are not sorted and unique")
    for path in checkpoint.expected_staged_paths:
        _safe_relative(path, "checkpoint path")
    if not SHA256.fullmatch(checkpoint.expected_staged_patch_sha256):
        raise ExecutorError("checkpoint patch digest is malformed")
    if type(checkpoint.forbid_unstaged) is not bool or type(checkpoint.forbid_untracked) is not bool:
        raise ExecutorError("checkpoint boolean contract is malformed")


def _validate_plan(plan: OperationPlan, request: Mapping[str, Any], pins: PinnedPaths) -> dict[str, str]:
    if not isinstance(plan, OperationPlan) or not isinstance(request, Mapping):
        raise ExecutorError("executor requires an OperationPlan and request mapping")
    if not UUID4.fullmatch(plan.request_id) or plan.request_id != request.get("id"):
        raise ExecutorError("plan/request ID mismatch")
    if plan.operation != request.get("operation") or plan.actor_id != request.get("actor_id"):
        raise ExecutorError("plan/request operation or actor mismatch")
    if not UUID4.fullmatch(plan.actor_id):
        raise ExecutorError("plan actor is not a canonical UUIDv4")
    if not isinstance(request.get("assignment"), str) or not ASSIGNMENT.fullmatch(request["assignment"]):
        raise ExecutorError("request Assignment is malformed")
    if type(request.get("epoch")) is not int or request["epoch"] < 1:
        raise ExecutorError("request epoch is malformed")
    if not isinstance(request.get("integration_head"), str) or not SHA40.fullmatch(request["integration_head"]):
        raise ExecutorError("request integration HEAD is malformed")
    if not isinstance(plan.role, ActorRole) or plan.stop_on_first_failure is not True:
        raise ExecutorError("plan role/stop policy is invalid")
    if plan.operation not in OPERATION_ROLES or plan.role not in OPERATION_ROLES[plan.operation]:
        raise ExecutorError("plan role is not authorized for this operation")
    if not isinstance(plan.commands, tuple) or not isinstance(plan.followups, tuple) or not isinstance(plan.checkpoints, tuple):
        raise ExecutorError("plan command collections must be immutable tuples")
    if len(plan.commands) > MAX_PLAN_COMMANDS or len(plan.followups) > 2:
        raise ExecutorError("plan exceeds the fixed command budget")
    for command in plan.commands:
        _validate_command(command, pins)
    for followup in plan.followups:
        _validate_followup(followup, pins)
    for checkpoint in plan.checkpoints:
        _validate_checkpoint(checkpoint, pins, len(plan.commands))
    indexes = [item.after_command_index for item in plan.checkpoints]
    if indexes != sorted(set(indexes)):
        raise ExecutorError("checkpoints must be unique and ordered")
    values = _bindings(plan, request)
    operation = plan.operation
    commands = plan.commands
    root, stage = pins.root, pins.stage_worktree
    target = root if plan.role is ActorRole.COORDINATOR else stage

    for key in {
        "worker-commit": ("expected_patch_sha256", "test_receipt_sha256"),
        "integrate": ("candidate_sha", "expected_patch_sha256", "verifier_receipt_sha256"),
        "verifier-submit": ("candidate_sha", "test_receipt_sha256"),
        "bundle-prepare": ("base_sha", "head_sha"),
        "review-request": ("base_sha", "head_sha"),
        "closeout-prepare": ("accepted_head", "final_receipt_sha256"),
        "closeout-review": ("accepted_head", "final_receipt_sha256"),
        "closeout-commit": ("accepted_head", "expected_patch_sha256", "closeout_receipt_sha256"),
        "closeout-advance": ("closeout_head", "closeout_commit_receipt_sha256"),
        "next-stage-prepare": ("closeout_commit_receipt_sha256",),
        "next-stage-commit": ("expected_patch_sha256", "prepare_receipt_sha256"),
        "next-stage-bind": ("next_stage_commit_receipt_sha256",),
        "receipt-check": ("receipt_sha256", "reviewed_sha"),
        "stage-advance": ("stage_head", "worker_receipt_sha256"),
    }.get(operation, ()):
        value = request.get(key)
        pattern = SHA256 if "sha256" in key else SHA40
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise ExecutorError(f"request {key} is malformed")
    if operation in {"worker-commit", "integrate", "closeout-commit", "next-stage-commit"}:
        if not SHA256.fullmatch(values.get("hook_sha256", "")) or not SHA256.fullmatch(
            values.get("memory_guard_sha256", "")
        ):
            raise ExecutorError("commit plan lacks protected hook/memory-guard bindings")

    if operation == "inspect":
        valid = len(commands) == 1 and _git_command(commands[0], target, ("rev-parse", "--show-toplevel", "HEAD"), EnvClass.READ_ONLY)
    elif operation == "status":
        valid = len(commands) == 1 and _status_command(commands[0], target)
    elif operation == "diff":
        valid_revision = commands[0].argv[7] if len(commands) == 1 and len(commands[0].argv) == 9 else ""
        valid = (
            len(commands) == 1
            and commands[0].env_class is EnvClass.READ_ONLY
            and commands[0].cwd == str(target)
            and commands[0].argv == (
                "git", "-C", str(target), "diff", "--no-ext-diff", "--binary", "--full-index", valid_revision, "--"
            )
            and (valid_revision == "HEAD" or bool(re.fullmatch(r"[0-9a-f]{40}\.\.HEAD", valid_revision)))
        )
    elif operation == "worker-check":
        valid = len(commands) == 1
        if valid:
            _parse_fence(commands[0], pins, request, expected_subcommand="worker-check", expected_head=str(request["integration_head"]))
    elif operation == "test":
        selectors = tuple(request.get("selectors", ()))
        if not selectors or any(not isinstance(value, str) or not TEST_SELECTOR.fullmatch(value) for value in selectors):
            raise ExecutorError("test request contains an unsupported selector")
        valid = (
            len(commands) == 4
            and _status_command(commands[0], stage)
            and _test_command(commands[2], stage, selectors)
            and _status_command(commands[3], stage)
        )
        if valid:
            _parse_fence(commands[1], pins, request, expected_subcommand="worker-check", expected_head=str(request["integration_head"]))
    elif operation == "verifier-submit":
        valid = not commands and not plan.followups and not plan.checkpoints
    elif operation == "worker-commit":
        changed = commands[1].argv[5:] if len(commands) == 5 and len(commands[1].argv) > 5 else ()
        valid = (
            len(commands) == 5
            and commands[1].env_class is EnvClass.GIT_MUTATION
            and commands[1].cwd == str(stage)
            and commands[1].argv[:5] == ("git", "-C", str(stage), "add", "--")
            and bool(changed)
            and _git_command(commands[2], stage, ("diff", "--cached", "--check"), EnvClass.READ_ONLY)
            and _git_command(commands[3], stage, ("commit", "-m", str(request.get("message"))), EnvClass.GIT_MUTATION)
            and _status_command(commands[4], stage)
            and len(plan.checkpoints) == 1
            and plan.checkpoints[0].after_command_index == 1
            and plan.checkpoints[0].worktree == str(stage)
            and plan.checkpoints[0].forbid_unstaged is True
            and plan.checkpoints[0].forbid_untracked is True
            and tuple(changed) == plan.checkpoints[0].expected_staged_paths
            and plan.checkpoints[0].expected_staged_patch_sha256 == request.get("expected_patch_sha256")
        )
        if valid:
            _parse_fence(commands[0], pins, request, expected_subcommand="worker-check", expected_head=str(request["integration_head"]))
    elif operation == "stage-advance":
        valid = len(commands) == 1
        if valid:
            _parse_fence(
                commands[0], pins, request, expected_subcommand="stage-advance",
                expected_head=str(request["integration_head"]), expected_stage_head=str(request.get("stage_head")),
            )
    elif operation == "integrate":
        selectors = tuple(request.get("test_selectors", ()))
        if not selectors or any(not isinstance(value, str) or not TEST_SELECTOR.fullmatch(value) for value in selectors):
            raise ExecutorError("integration request contains an unsupported selector")
        valid = (
            len(commands) == 6
            and _git_command(commands[1], root, ("cherry-pick", "--no-commit", str(request.get("candidate_sha"))), EnvClass.GIT_MUTATION)
            and _git_command(commands[2], root, ("diff", "--cached", "--check"), EnvClass.READ_ONLY)
            and _test_command(commands[3], root, selectors)
            and _git_command(commands[4], root, ("diff", "--cached", "--binary", "--full-index", "--"), EnvClass.READ_ONLY)
            and _git_command(commands[5], root, ("commit", "-m", str(request.get("message"))), EnvClass.GIT_MUTATION)
            and tuple(item.after_command_index for item in plan.checkpoints) == (1, 3)
            and all(item.worktree == str(root) for item in plan.checkpoints)
            and all(item.expected_head_sha == request.get("integration_head") for item in plan.checkpoints)
            and all(item.forbid_unstaged is True and item.forbid_untracked is True for item in plan.checkpoints)
            and plan.checkpoints[0].expected_staged_paths == plan.checkpoints[1].expected_staged_paths
            and all(item.expected_staged_patch_sha256 == request.get("expected_patch_sha256") for item in plan.checkpoints)
        )
        if valid:
            _parse_fence(commands[0], pins, request, expected_subcommand="check", expected_head=str(request["integration_head"]))
    elif operation in {"bundle-prepare", "closeout-prepare"}:
        valid = not commands and not plan.followups and not plan.checkpoints
    elif operation == "review-request":
        expected = (
            str(pins.final_review_bridge), "--stage", values["stage"], "--assignment", str(request["assignment"]),
            "--bundle", str(request.get("bundle")), "--base", str(request.get("base_sha")), "--head", str(request.get("head_sha")),
        )
        valid = len(commands) == 1 and commands[0].env_class is EnvClass.EXTERNAL_CODEX_REVIEW and commands[0].cwd == str(root) and commands[0].argv == expected
    elif operation == "closeout-review":
        expected = (
            str(pins.closeout_review_bridge), "--stage", values["stage"], "--assignment",
            str(request["assignment"]), "--bundle",
            f".local/two-site-refactor/review-bundles/{values['stage']}-{request['assignment']}",
            "--accepted-head", str(request.get("accepted_head")),
        )
        valid = (len(commands) == 1 and commands[0].env_class is EnvClass.EXTERNAL_CODEX_REVIEW
                 and commands[0].cwd == str(root) and commands[0].argv == expected)
    elif operation == "closeout-commit":
        valid = len(commands) == 1 and _git_command(
            commands[0], root, ("commit", "-m", str(request.get("message"))), EnvClass.GIT_MUTATION
        )
    elif operation == "closeout-advance":
        valid = len(commands) == 1
        if valid:
            _parse_fence(commands[0], pins, request, expected_subcommand="advance",
                         expected_head=str(request.get("closeout_head")))
    elif operation == "next-stage-prepare":
        valid = len(commands) == 1
        if valid:
            _parse_fence(commands[0], pins, request, expected_subcommand="stage-open-check",
                         expected_head=str(request["integration_head"]),
                         expected_extra=("--next-stage", str(request.get("next_stage"))))
    elif operation == "next-stage-commit":
        valid = len(commands) == 1 and _git_command(
            commands[0], root, ("commit", "-m", str(request.get("message"))), EnvClass.GIT_MUTATION
        )
    elif operation == "next-stage-bind":
        valid = len(commands) == 1
        if valid:
            _parse_fence(commands[0], pins, request, expected_subcommand="stage-bind",
                         expected_head=str(request["integration_head"]),
                         expected_extra=("--next-assignment", str(request.get("next_assignment"))))
    elif operation == "receipt-check":
        valid = not commands and not plan.followups and not plan.checkpoints
    else:
        raise ExecutorError("operation is outside the executor catalog")
    if not valid:
        raise ExecutorError(f"{operation} command grammar differs from the fixed executor contract")

    if operation not in {"worker-commit", "integrate"} and plan.checkpoints:
        raise ExecutorError(f"{operation} cannot carry Git checkpoints")

    if operation in {"integrate", "next-stage-commit"}:
        if len(plan.followups) != 1:
            raise ExecutorError(operation + " must have exactly one fence followup")
        followup = plan.followups[0]
        if followup.observed_value != RESULTING_HEAD:
            raise ExecutorError(operation + " followup has an unsupported observed value")
        _parse_fence(followup, pins, request, expected_subcommand="advance", expected_head=RESULTING_HEAD)
    elif operation == "closeout-commit":
        if len(plan.followups) != 1:
            raise ExecutorError("closeout must have exactly one verification followup")
        verify = plan.followups[0]
        expected_verify = (
            str(pins.closeout_review_bridge), "--verify-commit", "--stage", values["stage"],
            "--assignment", str(request["assignment"]), "--bundle",
            f".local/two-site-refactor/review-bundles/{values['stage']}-{request['assignment']}",
            "--accepted-head", str(request.get("accepted_head")), "--closeout-head", RESULTING_HEAD,
        )
        if (
            verify.env_class is not EnvClass.READ_ONLY
            or verify.cwd != str(root)
            or verify.argv_template != expected_verify
            or verify.observed_value != RESULTING_HEAD
        ):
            raise ExecutorError("closeout followup grammar differs from fixed contract")
    elif plan.followups:
        raise ExecutorError("operation has unexpected followup commands")
    return values


def _snapshot_pair(pins: PinnedPaths) -> tuple[GitSnapshot, GitSnapshot]:
    integration = observe_git(pins.root)
    stage = observe_git(pins.stage_worktree)
    if integration.branch != pins.integration_branch or stage.branch != pins.stage_branch:
        raise ExecutorError("observed branch differs from protected pin")
    _, integration_common = _git_layout(pins.root)
    _, stage_common = _git_layout(pins.stage_worktree)
    if integration_common != stage_common:
        raise ExecutorError("integration and Stage worktrees do not share one Git common directory")
    return integration, stage


def _snapshot_json(snapshot: GitSnapshot) -> dict[str, Any]:
    return {
        "worktree": str(snapshot.worktree),
        "branch": snapshot.branch,
        "head_sha": snapshot.head_sha,
        "parent_sha": snapshot.parent_sha,
        "staged_paths": list(snapshot.staged_paths),
        "unstaged_paths": list(snapshot.unstaged_paths),
        "untracked_paths": list(snapshot.untracked_paths),
        "worktree_patch_sha256": snapshot.worktree_patch_sha256,
        "staged_patch_sha256": snapshot.staged_patch_sha256,
    }


def _text_evidence(value: bytes | str) -> dict[str, Any]:
    raw = value.encode("utf-8", errors="replace") if isinstance(value, str) else bytes(value)
    text = raw[:MAX_EVIDENCE_TEXT].decode("utf-8", errors="replace")
    text = "".join(character if character in "\n\t" or ord(character) >= 32 else "�" for character in text)
    return {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "text": text,
        "truncated": len(raw) > MAX_EVIDENCE_TEXT,
    }


def _command_evidence(index: int, command: CommandPlan | FollowupCommandPlan, result: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
    return {
        "index": index,
        "purpose": command.purpose,
        "returncode": result.returncode,
        "stdout": _text_evidence(result.stdout),
        "stderr": _text_evidence(result.stderr),
    }


def _runner_result(result: subprocess.CompletedProcess[bytes]) -> subprocess.CompletedProcess[bytes]:
    if not isinstance(result, subprocess.CompletedProcess) or type(result.returncode) is not int:
        raise ExecutorError("command runner returned an invalid result")
    if not isinstance(result.stdout, bytes) or not isinstance(result.stderr, bytes):
        raise ExecutorError("command runner output must be bytes")
    if len(result.stdout) + len(result.stderr) > MAX_COMMAND_OUTPUT:
        raise ExecutorError("command runner exceeded the executor evidence budget")
    return result


def _actual_argv(command: CommandPlan | FollowupCommandPlan, pins: PinnedPaths, resulting_head: str | None = None) -> tuple[str, ...]:
    template = command.argv if isinstance(command, CommandPlan) else command.argv_template
    if isinstance(command, FollowupCommandPlan):
        if resulting_head is None or not SHA40.fullmatch(resulting_head):
            raise ExecutorError("followup lacks an independently observed resulting HEAD")
        if template.count(RESULTING_HEAD) != 1:
            raise ExecutorError("followup must contain exactly one resulting-HEAD placeholder")
        template = tuple(resulting_head if value == RESULTING_HEAD else value for value in template)
    if template[0] == "git":
        return (str(pins.git_binary), *template[1:])
    if template[0] == sys.executable:
        return (str(pins.python_binary), *template[1:])
    if template[0] in {str(pins.final_review_bridge), str(pins.closeout_review_bridge)}:
        return template
    raise ExecutorError("command executable is not pinned")


def _is_git_mutation(command: CommandPlan | FollowupCommandPlan) -> bool:
    return command.env_class is EnvClass.GIT_MUTATION


def _is_fence_mutation(command: CommandPlan | FollowupCommandPlan) -> bool:
    argv = command.argv if isinstance(command, CommandPlan) else command.argv_template
    return command.env_class is EnvClass.FENCE and len(argv) > 2 and argv[2] in {
        "stage-advance", "advance", "stage-bind",
    }


def _checkpoint(snapshot: GitSnapshot, checkpoint: GitCheckpoint) -> None:
    if snapshot.worktree != Path(checkpoint.worktree):
        raise ExecutorError("checkpoint observed the wrong worktree")
    if snapshot.head_sha != checkpoint.expected_head_sha:
        raise ExecutorError("checkpoint HEAD differs from plan")
    if snapshot.staged_paths != checkpoint.expected_staged_paths:
        raise ExecutorError("checkpoint staged paths differ from plan")
    if snapshot.staged_patch_sha256 != checkpoint.expected_staged_patch_sha256:
        raise ExecutorError("checkpoint staged patch differs from plan")
    if checkpoint.forbid_unstaged and snapshot.unstaged_paths:
        raise ExecutorError("checkpoint forbids unstaged changes")
    if checkpoint.forbid_untracked and snapshot.untracked_paths:
        raise ExecutorError("checkpoint forbids untracked changes")


def _commit_patch_sha(worktree: Path, parent: str, head: str) -> str:
    if not SHA40.fullmatch(parent) or not SHA40.fullmatch(head):
        raise ExecutorError("commit patch boundary is malformed")
    patch = _bounded_git(
        worktree,
        ("diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv", "--no-renames", parent, head, "--"),
    ).stdout
    if not patch:
        raise ExecutorError("resulting commit patch is empty")
    return hashlib.sha256(patch).hexdigest()


def _verify_commit_guard(worktree: Path, bindings: Mapping[str, str]) -> None:
    """Recheck the exact hook and main-branch memory guard used by real Git commit."""

    expected_hook = bindings.get("hook_sha256", "")
    expected_memory = bindings.get("memory_guard_sha256", "")
    if not SHA256.fullmatch(expected_hook) or not SHA256.fullmatch(expected_memory):
        raise ExecutorError("commit plan lacks protected hook/memory-guard digests")
    configured = _bounded_git(
        worktree,
        ("config", "--null", "--get", "core.hooksPath"),
        allowed_exit_codes=(0, 1),
        output_limit=4096,
        mutation_config=True,
    )
    if configured.returncode == 0 and configured.stdout.strip(b"\0"):
        raise ExecutorError("alternate core.hooksPath is forbidden at commit time")
    _, common = _git_layout(worktree)
    hook = common / "hooks" / "pre-commit"
    _canonical_file(hook, "pre-commit hook", executable=True)
    expected_lines = (
        "#!/bin/sh",
        "set -eu",
        "",
        "git diff --cached --check",
        "git show main:scripts/check_memory_secrets.py | python3 -",
    )
    try:
        hook_lines = hook.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ExecutorError("pre-commit hook cannot be read safely") from exc
    if tuple(hook_lines) != expected_lines or _file_sha256(hook) != expected_hook:
        raise ExecutorError("pre-commit hook differs from the protected reviewed guard")
    memory_guard = _bounded_git(
        worktree,
        ("show", "main:scripts/check_memory_secrets.py"),
        output_limit=MAX_COMMAND_OUTPUT,
        mutation_config=True,
    ).stdout
    if hashlib.sha256(memory_guard).hexdigest() != expected_memory:
        raise ExecutorError("main memory-secret guard differs from the protected pin")


def _blocked(plan: OperationPlan, message: str) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "request_id": getattr(plan, "request_id", "UNKNOWN"),
        "operation": getattr(plan, "operation", "UNKNOWN"),
        "reason": message,
        "authority": "EXECUTOR_VERIFIED_NO_APPROVAL",
    }


def _failed(
    plan: OperationPlan,
    message: str,
    evidence: Sequence[Mapping[str, Any]],
    pins: PinnedPaths,
) -> dict[str, Any]:
    try:
        snapshots = [_snapshot_json(item) for item in _snapshot_pair(pins)]
        observation_error = None
    except ExecutorError as exc:
        # Conflict/unmerged states are valid recovery evidence after a failed Git
        # mutation, even though they intentionally cannot become a GitSnapshot.
        snapshots = []
        observation_error = str(exc)
    return {
        "status": "FAILED",
        "request_id": plan.request_id,
        "operation": plan.operation,
        "reason": message,
        "commands": list(evidence),
        "post_git": snapshots,
        "post_git_observation_error": observation_error,
        "authority": "EXECUTOR_VERIFIED_NO_APPROVAL",
    }


def execute_plan(
    plan: OperationPlan,
    request: Mapping[str, Any],
    *,
    runner: OperationRunner,
    pinned_paths: PinnedPaths,
    before_mutation: BeforeMutation,
    review_supervisor: ReviewSupervisor | None = None,
) -> dict[str, Any]:
    """Execute one already-authorized fixed plan and verify every Git boundary."""

    try:
        _validate_pins(pinned_paths)
        bindings = _validate_plan(plan, request, pinned_paths)
        if not callable(before_mutation):
            raise ExecutorError("protected before_mutation callback is required")
        if plan.operation == "receipt-check":
            return {
                "status": "SUCCESS",
                "request_id": plan.request_id,
                "operation": plan.operation,
                "commands": [],
                "pre_git": [],
                "post_git": [],
                "resulting_head": None,
                "authority": "EXECUTOR_VERIFIED_NO_APPROVAL",
                "receipt_decision": None,
            }
        pre = current = _snapshot_pair(pinned_paths)
        expected_integration_head = (request.get("closeout_head")
                                     if plan.operation == "closeout-advance"
                                     else request.get("integration_head"))
        if pre[0].head_sha != expected_integration_head:
            raise ExecutorError("integration HEAD became stale before execution")
        if plan.operation == "worker-commit":
            if not plan.checkpoints or pre[1].head_sha != plan.checkpoints[0].expected_head_sha:
                raise ExecutorError("Worker HEAD became stale before commit")
        if plan.operation == "stage-advance" and pre[1].head_sha != request.get("stage_head"):
            raise ExecutorError("Stage HEAD became stale before registry advance")
        if plan.operation == "integrate" and pre[1].head_sha != request.get("candidate_sha"):
            raise ExecutorError("candidate HEAD differs from registered integration request")
        if plan.operation == "closeout-commit":
            if pre[0].staged_patch_sha256 != request.get("expected_patch_sha256"):
                raise ExecutorError("closeout staged patch became stale before commit")
            expected_closeout = tuple(
                sorted(
                    (
                        "docs/refactor/two-server/CHANGE_LEDGER.md",
                        "docs/refactor/two-server/execution-order.yaml",
                        f"docs/refactor/two-server/reviews/{bindings['stage']}-{request['assignment']}.json",
                        f"docs/refactor/two-server/reviews/{bindings['stage']}-{request['assignment']}.md",
                    )
                )
            )
            if pre[0].staged_paths != expected_closeout or pre[0].unstaged_paths or pre[0].untracked_paths:
                raise ExecutorError("closeout index is not the exact clean canonical four-path patch")
        if plan.operation == "next-stage-commit":
            if (pre[0].staged_paths != ("docs/refactor/two-server/execution-order.yaml",)
                    or pre[0].unstaged_paths or pre[0].untracked_paths
                    or pre[0].staged_patch_sha256 != request.get("expected_patch_sha256")):
                raise ExecutorError("next Stage transition index became stale before commit")
        if plan.operation in {"review-request", "closeout-review"} and review_supervisor is None:
            return _blocked(plan, "EXTERNAL_CODEX_REVIEW requires the protected review supervisor")
    except ExecutorError as exc:
        return _blocked(plan, str(exc))

    evidence: list[dict[str, Any]] = []
    review_result: Mapping[str, Any] | None = None
    resulting_head: str | None = None
    for index, command in enumerate(plan.commands):
        try:
            observed_before = _snapshot_pair(pinned_paths)
            if observed_before != current:
                raise ExecutorError("Git state drifted between authorized command boundaries")
            mutating = _is_git_mutation(command) or _is_fence_mutation(command)
            if mutating:
                before_mutation(
                    MutationBoundary(plan.request_id, plan.operation, "command", index, command, *observed_before)
                )
                if _snapshot_pair(pinned_paths) != observed_before:
                    raise ExecutorError("Git state changed inside before_mutation policy")

            if command.env_class is EnvClass.HERMETIC_TEST:
                selectors = command.argv[command.argv.index("--") + 1 :]
                test_result = runner.run_hermetic(
                    Path(command.cwd), selectors, expected_head=observed_before[0].head_sha
                    if Path(command.cwd) == pinned_paths.root else observed_before[1].head_sha,
                    extra_paths=(observed_before[0].untracked_paths if Path(command.cwd) == pinned_paths.root
                                 else observed_before[1].untracked_paths),
                    timeout=TEST_TIMEOUT,
                )
                try:
                    encoded = json.dumps(test_result, sort_keys=True, allow_nan=False).encode("utf-8")
                except (TypeError, ValueError, RecursionError) as exc:
                    raise ExecutorError("hermetic runner returned invalid evidence") from exc
                if len(encoded) > 8_000_000 or type(test_result.get("returncode")) is not int:
                    raise ExecutorError("hermetic runner evidence exceeds its contract")
                result = subprocess.CompletedProcess(
                    command.argv,
                    int(test_result["returncode"]),
                    str(test_result.get("stdout", "")).encode("utf-8", errors="replace"),
                    str(test_result.get("stderr", "")).encode("utf-8", errors="replace"),
                )
            elif command.env_class is EnvClass.EXTERNAL_CODEX_REVIEW:
                assert review_supervisor is not None
                supervisor_result = review_supervisor(command, request, bindings)
                try:
                    encoded = json.dumps(supervisor_result, sort_keys=True, allow_nan=False).encode("utf-8")
                except (TypeError, ValueError, RecursionError) as exc:
                    raise ExecutorError("review supervisor returned invalid bounded evidence") from exc
                if len(encoded) > MAX_COMMAND_OUTPUT or supervisor_result.get("status") not in {"SUCCESS", "FAILED", "BLOCKED"}:
                    raise ExecutorError("review supervisor returned an unsupported result")
                if supervisor_result.get("status") == "SUCCESS":
                    receipt_sha = supervisor_result.get("receipt_file_sha256")
                    receipt_path = supervisor_result.get("receipt_path")
                    if (not isinstance(receipt_sha, str) or not SHA256.fullmatch(receipt_sha)
                            or not isinstance(receipt_path, str) or not receipt_path):
                        raise ExecutorError("successful review lacks an exact output receipt binding")
                    review_result = supervisor_result
                result = subprocess.CompletedProcess(
                    command.argv,
                    0 if supervisor_result["status"] == "SUCCESS" else 1,
                    encoded,
                    b"",
                )
            else:
                _validate_pins(pinned_paths)
                argv = _actual_argv(command, pinned_paths)
                if command.env_class is EnvClass.GIT_MUTATION and "commit" in command.argv:
                    _verify_commit_guard(Path(command.cwd), bindings)
                timeout = MUTATION_TIMEOUT if command.env_class is EnvClass.GIT_MUTATION else (
                    FENCE_TIMEOUT if command.env_class is EnvClass.FENCE else READ_TIMEOUT
                )
                result = runner.run_command(
                    argv,
                    cwd=Path(command.cwd),
                    env=_safe_env(mutation=command.env_class is EnvClass.GIT_MUTATION),
                    timeout=timeout,
                    output_limit=MAX_COMMAND_OUTPUT,
                )
            result = _runner_result(result)
            evidence.append(_command_evidence(index, command, result))
            observed_after = _snapshot_pair(pinned_paths)
            if command.env_class is not EnvClass.GIT_MUTATION and observed_after != observed_before:
                raise ExecutorError("read-only/test/fence command changed Git state")
            current = observed_after
            if result.returncode not in command.expected_exit_codes:
                return _failed(plan, "command returned a non-success exit code", evidence, pinned_paths)
            for checkpoint in plan.checkpoints:
                if checkpoint.after_command_index == index:
                    selected = current[0] if checkpoint.worktree == str(pinned_paths.root) else current[1]
                    _checkpoint(selected, checkpoint)
        except ExecutorError as exc:
            return _failed(plan, str(exc), evidence, pinned_paths)

    try:
        if plan.operation == "worker-commit":
            expected_patch = str(request["expected_patch_sha256"])
            if current[0] != pre[0] or not current[1].clean or current[1].parent_sha != pre[1].head_sha:
                raise ExecutorError("Worker commit is not one clean direct child of its observed HEAD")
            if _commit_patch_sha(pinned_paths.stage_worktree, pre[1].head_sha, current[1].head_sha) != expected_patch:
                raise ExecutorError("Worker commit patch differs from the authorized candidate patch")
            resulting_head = current[1].head_sha
        elif plan.operation == "integrate":
            expected_patch = str(request["expected_patch_sha256"])
            if current[1] != pre[1] or not current[0].clean or current[0].parent_sha != pre[0].head_sha:
                raise ExecutorError("integration commit is not one clean direct child of its observed HEAD")
            if _commit_patch_sha(pinned_paths.root, pre[0].head_sha, current[0].head_sha) != expected_patch:
                raise ExecutorError("integration commit patch differs from the verified candidate patch")
            resulting_head = current[0].head_sha
        elif plan.operation == "closeout-commit":
            expected_patch = str(request["expected_patch_sha256"])
            if current[1] != pre[1] or not current[0].clean or current[0].parent_sha != pre[0].head_sha:
                raise ExecutorError("closeout commit is not one clean direct child of the accepted HEAD")
            if _commit_patch_sha(pinned_paths.root, pre[0].head_sha, current[0].head_sha) != expected_patch:
                raise ExecutorError("closeout commit patch differs from the pre-action-reviewed patch")
            resulting_head = current[0].head_sha
        elif plan.operation == "next-stage-commit":
            expected_patch = str(request["expected_patch_sha256"])
            if current[1] != pre[1] or not current[0].clean or current[0].parent_sha != pre[0].head_sha:
                raise ExecutorError("next Stage transition is not one clean direct child")
            if _commit_patch_sha(pinned_paths.root, pre[0].head_sha, current[0].head_sha) != expected_patch:
                raise ExecutorError("next Stage transition commit differs from prepared patch")
            resulting_head = current[0].head_sha
        elif current != pre:
            raise ExecutorError("read-only/test/state operation changed Git state")
    except ExecutorError as exc:
        return _failed(plan, str(exc), evidence, pinned_paths)

    for offset, followup in enumerate(plan.followups):
        try:
            _validate_pins(pinned_paths)
            before = _snapshot_pair(pinned_paths)
            if before != current:
                raise ExecutorError("Git state drifted before a followup")
            if _is_fence_mutation(followup):
                before_mutation(
                    MutationBoundary(plan.request_id, plan.operation, "followup", offset, followup, *before)
                )
                if _snapshot_pair(pinned_paths) != before:
                    raise ExecutorError("Git state changed inside followup before_mutation policy")
            argv = _actual_argv(followup, pinned_paths, resulting_head)
            result = _runner_result(
                runner.run_command(
                    argv,
                    cwd=Path(followup.cwd),
                    env=_safe_env(),
                    timeout=FENCE_TIMEOUT if followup.env_class is EnvClass.FENCE else READ_TIMEOUT,
                    output_limit=MAX_COMMAND_OUTPUT,
                )
            )
            evidence.append(_command_evidence(len(plan.commands) + offset, followup, result))
            after = _snapshot_pair(pinned_paths)
            if after != before:
                raise ExecutorError("followup changed Git state")
            current = after
            if result.returncode != 0:
                return _failed(plan, "followup returned a non-success exit code", evidence, pinned_paths)
        except ExecutorError as exc:
            return _failed(plan, str(exc), evidence, pinned_paths)

    outcome = {
        "status": "SUCCESS",
        "request_id": plan.request_id,
        "operation": plan.operation,
        "commands": evidence,
        "pre_git": [_snapshot_json(item) for item in pre],
        "post_git": [_snapshot_json(item) for item in current],
        "resulting_head": resulting_head,
        "authority": "EXECUTOR_VERIFIED_NO_APPROVAL",
        "receipt_decision": None,
    }
    if review_result is not None:
        outcome["review_output"] = {
            "receipt_path": review_result["receipt_path"],
            "receipt_file_sha256": review_result["receipt_file_sha256"],
            "markdown_path": review_result.get("markdown_path"),
            "markdown_file_sha256": review_result.get("markdown_file_sha256"),
        }
    return outcome


__all__ = [
    "ExecutorError",
    "LocalOperationRunner",
    "MutationBoundary",
    "OperationRunner",
    "PinnedPaths",
    "ReviewSupervisor",
    "execute_plan",
    "observe_git",
]
