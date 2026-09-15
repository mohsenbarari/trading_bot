#!/usr/bin/env python3
"""Run bounded non-interactive Codex cycles until completion or a real blocker."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time

try:
    from scripts.two_site_proposal_runner import (
        MAX_CONTEXT,
        GEMINI_RUNTIME_CONTRACT_REL,
        MODEL as GEMINI_MODEL,
        WORKER_MODEL as GEMINI_WORKER_MODEL,
        ProposalError,
        bounded_file_text,
        capture_bounded_process,
        file_sha256,
        file_fingerprint,
        git,
        terminate_process,
        unique_json,
        validate_runtime_contract,
        validate_result as validate_proposal_result,
        write_json,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        MAX_CONTEXT,
        GEMINI_RUNTIME_CONTRACT_REL,
        MODEL as GEMINI_MODEL,
        WORKER_MODEL as GEMINI_WORKER_MODEL,
        ProposalError,
        bounded_file_text,
        capture_bounded_process,
        file_sha256,
        file_fingerprint,
        git,
        terminate_process,
        unique_json,
        validate_runtime_contract,
        validate_result as validate_proposal_result,
        write_json,
    )
try:
    from scripts.two_site_assignment_scope import (
        AssignmentScope,
        AssignmentScopeError,
        load_active_assignment_scope,
        validate_changed_paths,
        validate_task_scope,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    from two_site_assignment_scope import (  # type: ignore[no-redef]
        AssignmentScope,
        AssignmentScopeError,
        load_active_assignment_scope,
        validate_changed_paths,
        validate_task_scope,
    )


ROOT = Path(__file__).resolve().parents[1]
BRANCH = "refactor/two-site-architecture"
MODEL = "gpt-5.6-sol"
REASONING = "xhigh"
EXPECTED_CODEX_CLI_VERSION = "codex-cli 0.153.4"
EXPECTED_CODEX_CLI_SHA256 = "56ef98ab4032d317ab26e9b5e5a175650717351edb16ed9cde0cb6d1734d62da"
CODEX_RUNTIME_FILES = {
    "codex-code-mode-host": "3e85d67471825f73d02ff5f7e047ca1f6ca8caa3f59e4c6e8d9ca6ca7302cb45",
    "codex-package.json": "43f8735a93a7947c6dc082c550d2ff2d795fccfd1c7eb27c93aa91cbead1e3f4",
    "codex-path/rg": "e62198eb19b136b88c330af83647b5a962cb99b6b1f066758568f12de1974849",
    "codex-resources/bwrap": "77360cb751ccedc5971391444ac86a8a33c15b04d6b4a6fe45f5d25496e62c4c",
    "codex-resources/zsh/bin/zsh": "67faaaa89242c4a332e16e508a1977cffc24bf7fca31d4411cdfd101f3831ef3",
    "rg": "e62198eb19b136b88c330af83647b5a962cb99b6b1f066758568f12de1974849",
}
MAX_CODEX_OUTPUT = 16_000_000
MAX_RESULT_BYTES = 64_000
MAX_PROMPT_BYTES = 160_000
DEFAULT_MAX_CYCLES = 200
DEFAULT_TIMEOUT_SECONDS = 10_800
MAX_RETRYABLE_FAILURES = 2
MAX_CONSECUTIVE_RESCOPES = 3
KEEP_RUNS = 20
SUPERVISOR_HEARTBEAT_SECONDS = 10
PROMPT_REL = Path("docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md")
SCHEMA_REL = Path("docs/refactor/two-server/templates/codex-autopilot-cycle.schema.json")
PROTECTED_REFS = (
    "refs/heads/main",
    "refs/remotes/origin/main",
    "refs/heads/candidate/wa-ir-standby-v1",
)
RUN_ID = re.compile(r"cycle-[0-9]{4}-[0-9]{8}T[0-9]{6}Z-[0-9]+")
RESULT_KEYS = {
    "schema_version", "state", "disposition", "category", "summary", "commit", "next_action",
    "blockers", "gemini_worker_run_id", "gemini_reviewer_run_id", "proposal_sha256",
}
PROPOSAL_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
PRUNABLE_RUN_STATES = {"CONTINUE", "COMPLETE", "FAILED", "RETRYABLE_FAILURE", "STOPPED"}
CONTROL_PATHS = (
    Path("scripts/two_site_codex_autopilot.py"),
    Path("scripts/two_site_cycle_packet.py"),
    Path("scripts/two_site_cycle_tools.py"),
    Path("scripts/two_site_cycle_metrics.py"),
    Path("scripts/two_site_proposal_runner.py"),
    Path("scripts/two_site_provider_broker.py"),
    Path("scripts/two_site_assignment_scope.py"),
    Path("scripts/two_site_autopilot_tmux.sh"),
    Path("scripts/two_site_autopilot_monitor.sh"),
    GEMINI_RUNTIME_CONTRACT_REL,
    Path("docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md"),
    PROMPT_REL,
    SCHEMA_REL,
    Path(".agents/skills/two-site-refactor/SKILL.md"),
    Path(".agents/rules/two-site-refactor-safety.md"),
)
_ACTIVE_PROCESS: subprocess.Popen | None = None


class AutopilotError(ProposalError):
    pass


@contextmanager
def exclusive_supervisor_lock(lock_path: Path):
    """Own one symlink/hardlink-safe nonblocking supervisor lock."""
    flags = (os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise AutopilotError("cannot safely open autopilot lock") from exc
    owned = False
    try:
        lock_stat = os.fstat(descriptor)
        if (not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1
                or lock_stat.st_uid != os.geteuid()):
            raise AutopilotError("autopilot lock is not a trusted regular file")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AutopilotError("another autopilot supervisor already owns the lock") from exc
        owned = True
        yield
    finally:
        if owned:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def contains_schema_keyword(value: object, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return bool(set(value) & forbidden) or any(
            contains_schema_keyword(child, forbidden) for child in value.values()
        )
    if isinstance(value, list):
        return any(contains_schema_keyword(child, forbidden) for child in value)
    return False


def ref_snapshot(root: Path) -> dict[str, str | None]:
    result = {}
    for ref in PROTECTED_REFS:
        try:
            result[ref] = git(root, "show-ref", "--verify", "--hash", ref)
        except subprocess.CalledProcessError:
            result[ref] = None
    return result


def validate_repository(root: Path, require_clean: bool = True) -> dict[str, object]:
    root = root.resolve(strict=True)
    # This is deliberately the first control gate: no Codex cycle or Gemini
    # Worker/Reviewer invocation is allowed while the runtime contract and its
    # human-readable references disagree.
    validate_runtime_contract(root)
    if git(root, "branch", "--show-current") != BRANCH:
        raise AutopilotError(f"autopilot requires branch {BRANCH}")
    head = git(root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise AutopilotError("integration HEAD is invalid")
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if require_clean and status:
        raise AutopilotError("integration worktree is not clean")
    controls = {}
    for relative in CONTROL_PATHS:
        path = root / relative
        if (not path.is_file() or path.resolve() != path
                or path.stat(follow_symlinks=False).st_nlink != 1):
            raise AutopilotError(f"required tracked input is missing or unsafe: {relative}")
        git(root, "cat-file", "-e", f"HEAD:{relative.as_posix()}")
        controls[relative.as_posix()] = git(root, "rev-parse", f"HEAD:{relative.as_posix()}")
    schema = unique_json(bounded_file_text(
        root, root / SCHEMA_REL, MAX_RESULT_BYTES, "autopilot schema"
    ))
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if (
        not isinstance(schema, dict)
        or schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or set(schema.get("required", [])) != RESULT_KEYS
        or not isinstance(properties, dict)
        or set(properties) != RESULT_KEYS
        or any(not isinstance(value, dict) or "type" not in value for value in properties.values())
        or contains_schema_keyword(schema, {"oneOf", "anyOf", "allOf"})
    ):
        raise AutopilotError("autopilot result schema is invalid")
    try:
        assignment_scope = load_active_assignment_scope(root)
    except AssignmentScopeError as exc:
        raise AutopilotError("assignment scope validation failed") from exc
    return {
        "head": head,
        "status": status,
        "protected_refs": ref_snapshot(root),
        "control_blobs": controls,
        "assignment_scope": assignment_scope,
    }


def assignment_scope_context(scope: object) -> dict[str, object]:
    if not isinstance(scope, AssignmentScope):
        raise AutopilotError("invalid or missing assignment scope")
    return {
        "assignment_id": scope.assignment_id,
        "stage_id": scope.stage_id,
        "sha256": scope.sha256,
        "expires_at": scope.expires_at,
        "allowed_paths": list(scope.allowed_paths),
        "forbidden_paths": list(scope.forbidden_paths),
    }


def codex_runtime_identity(codex: Path) -> dict[str, str]:
    """Validate the complete matching runtime, not merely the main executable."""
    identity = {}
    for name, expected in CODEX_RUNTIME_FILES.items():
        path = codex.parent / name
        if path.resolve() != path:
            raise AutopilotError("Codex runtime path alias: " + name)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as exc:
            raise AutopilotError("cannot open Codex runtime file: " + name) from exc
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_uid != os.geteuid() or before.st_mode & 0o222
                    or (name != "codex-package.json" and not before.st_mode & 0o111)
                    or before.st_size > 100_000_000):
                raise AutopilotError("untrusted Codex runtime file: " + name)
            digest = hashlib.sha256()
            count = 0
            while chunk := stream.read(1024 * 1024):
                count += len(chunk)
                if count > 100_000_000:
                    raise AutopilotError("Codex runtime file grew: " + name)
                digest.update(chunk)
            after = os.fstat(stream.fileno())
            if (file_fingerprint(before) != file_fingerprint(after)
                    or file_fingerprint(after) != file_fingerprint(path.stat(follow_symlinks=False))
                    or path.resolve() != path or digest.hexdigest() != expected):
                raise AutopilotError("Codex runtime identity mismatch: " + name)
            identity[name] = digest.hexdigest()
    return identity


def codex_identity(codex: Path, temporary: Path) -> dict[str, object]:
    codex = codex.resolve(strict=True)
    if not codex.is_file() or not os.access(codex, os.X_OK):
        raise AutopilotError("Codex CLI must be an executable regular file")
    digest = file_sha256(codex)
    if digest != EXPECTED_CODEX_CLI_SHA256:
        raise AutopilotError("Codex CLI binary hash is not the reviewed pin")
    runtime = codex_runtime_identity(codex)
    with subprocess.Popen(
        [str(codex), "--version"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        capture_bounded_process(
            process, b"", temporary / "codex-version.stdout",
            temporary / "codex-version.stderr", 15, max_output=4096,
        )
    version = bounded_file_text(
        temporary, temporary / "codex-version.stdout", 4096, "Codex version output"
    ).strip()
    if process.returncode or version != EXPECTED_CODEX_CLI_VERSION:
        raise AutopilotError("Codex CLI version probe failed")
    with subprocess.Popen(
        [str(codex), "login", "status"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        capture_bounded_process(
            process, b"", temporary / "codex-login.stdout",
            temporary / "codex-login.stderr", 15, max_output=4096,
        )
    if process.returncode:
        raise AutopilotError("Codex CLI is not authenticated")
    if file_sha256(codex) != digest:
        raise AutopilotError("Codex CLI identity changed during readiness inspection")
    if codex_runtime_identity(codex) != runtime:
        raise AutopilotError("Codex runtime changed during readiness inspection")
    return {
        "path": str(codex), "version": version, "sha256": digest,
        "model": MODEL, "reasoning": REASONING, "runtime": runtime,
    }


def validate_result(result: object) -> dict:
    if not isinstance(result, dict) or set(result) != RESULT_KEYS:
        raise AutopilotError("Codex result does not match the exact contract")
    if result["schema_version"] != "1.0" or result["state"] not in {
        "CONTINUE", "COMPLETE", "BLOCKED",
    }:
        raise AutopilotError("Codex result has an invalid schema version or state")
    if result["disposition"] not in {"NONE", "HALT", "RESCOPE"}:
        raise AutopilotError("Codex result has an invalid disposition")
    if result["category"] not in {"NONE", "INTERNAL_REJECTION", "EXTERNAL_GATE"}:
        raise AutopilotError("Codex result has an invalid category")
    if result["state"] in {"CONTINUE", "COMPLETE"}:
        if result["disposition"] != "NONE":
            raise AutopilotError("CONTINUE and COMPLETE require disposition NONE")
        if result["category"] != "NONE":
            raise AutopilotError("CONTINUE and COMPLETE require category NONE")
    if result["state"] == "BLOCKED":
        if result["disposition"] not in {"HALT", "RESCOPE"}:
            raise AutopilotError("BLOCKED requires disposition HALT or RESCOPE")
        if result["disposition"] == "RESCOPE" and result["category"] != "INTERNAL_REJECTION":
            raise AutopilotError("BLOCKED/RESCOPE requires category INTERNAL_REJECTION")
        if result["disposition"] == "HALT" and result["category"] != "EXTERNAL_GATE":
            raise AutopilotError("BLOCKED/HALT requires category EXTERNAL_GATE")
    if not isinstance(result["summary"], str) or not result["summary"].strip():
        raise AutopilotError("Codex result summary is missing")
    if result["commit"] is not None and not (
        isinstance(result["commit"], str)
        and re.fullmatch(r"[0-9a-f]{40}", result["commit"])
    ):
        raise AutopilotError("Codex result commit is invalid")
    if result["next_action"] is not None and not (
        isinstance(result["next_action"], str) and result["next_action"].strip()
    ):
        raise AutopilotError("Codex result next_action is invalid")
    blockers = result["blockers"]
    if (
        not isinstance(blockers, list)
        or len(blockers) > 20
        or any(not isinstance(item, str) or not item.strip() for item in blockers)
    ):
        raise AutopilotError("Codex result blockers are invalid")
    if result["state"] == "BLOCKED" and not blockers:
        raise AutopilotError("BLOCKED requires at least one concrete blocker")
    if result["state"] != "BLOCKED" and blockers:
        raise AutopilotError("non-blocked state cannot report blockers")
    if result["state"] == "CONTINUE" and result["next_action"] is None:
        raise AutopilotError("CONTINUE requires a concrete next action")
    evidence = (
        result["gemini_worker_run_id"], result["gemini_reviewer_run_id"],
        result["proposal_sha256"],
    )
    if any(value is None for value in evidence) != all(value is None for value in evidence):
        raise AutopilotError("Gemini evidence fields must be all present or all null")
    if result["commit"] is not None:
        if (any(value is None for value in evidence)
                or any(not isinstance(value, str) for value in evidence)
                or not PROPOSAL_RUN_ID.fullmatch(result["gemini_worker_run_id"])
                or not PROPOSAL_RUN_ID.fullmatch(result["gemini_reviewer_run_id"])
                or not re.fullmatch(r"[0-9a-f]{64}", result["proposal_sha256"])):
            raise AutopilotError("commit requires exact Gemini Worker/Reviewer evidence")
    elif any(value is not None for value in evidence):
        raise AutopilotError("Gemini acceptance evidence cannot accompany a null commit")
    if result["disposition"] in {"HALT", "RESCOPE"}:
        if result["commit"] is not None or any(value is not None for value in evidence):
            raise AutopilotError(f"{result['disposition']} requires null commit and Gemini acceptance evidence")
    if result["disposition"] == "RESCOPE" and result["next_action"] is None:
        raise AutopilotError("RESCOPE requires a concrete next action")
    return result


def recover_committed_advisory_result(
    raw_result: object,
    validation_error: Exception,
) -> tuple[dict, list[str]]:
    """Normalize a committed CONTINUE receipt that erroneously reported non-blocking blockers."""
    if (
        not isinstance(validation_error, AutopilotError)
        or str(validation_error) != "non-blocked state cannot report blockers"
    ):
        raise AutopilotError(f"unrecoverable validation error: {validation_error}")
    if not isinstance(raw_result, dict) or set(raw_result) != RESULT_KEYS:
        raise AutopilotError("Codex result does not match the exact contract")
    if raw_result.get("state") != "CONTINUE":
        raise AutopilotError("recovery only applies to CONTINUE state")
    if raw_result.get("disposition") != "NONE" or raw_result.get("category") != "NONE":
        raise AutopilotError("recovery requires disposition NONE and category NONE")
    commit = raw_result.get("commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise AutopilotError("recovery requires a valid 40-hex commit SHA")
    next_action = raw_result.get("next_action")
    if not isinstance(next_action, str) or not next_action.strip():
        raise AutopilotError("recovery requires a non-empty next_action")
    worker_run_id = raw_result.get("gemini_worker_run_id")
    reviewer_run_id = raw_result.get("gemini_reviewer_run_id")
    proposal_sha256 = raw_result.get("proposal_sha256")
    if (
        not isinstance(worker_run_id, str)
        or not PROPOSAL_RUN_ID.fullmatch(worker_run_id)
        or not isinstance(reviewer_run_id, str)
        or not PROPOSAL_RUN_ID.fullmatch(reviewer_run_id)
        or not isinstance(proposal_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", proposal_sha256)
    ):
        raise AutopilotError("recovery requires exact Gemini Worker/Reviewer evidence")
    blockers = raw_result.get("blockers")
    if (
        not isinstance(blockers, list)
        or not (1 <= len(blockers) <= 20)
        or any(not isinstance(item, str) or not item.strip() for item in blockers)
    ):
        raise AutopilotError("recovery requires a non-empty bounded list of string blockers")
    candidate = dict(raw_result)
    candidate["blockers"] = []
    advisories = list(blockers)
    normalized = validate_result(candidate)
    return normalized, advisories


def proposal_run_dir(root: Path, run_id: str) -> Path:
    if not PROPOSAL_RUN_ID.fullmatch(run_id):
        raise AutopilotError("proposal evidence run ID is invalid")
    proposals = root / ".local/two-site-refactor/proposals"
    run_dir = proposals / run_id
    if (not run_dir.is_dir() or run_dir.resolve() != run_dir
            or run_dir.parent.resolve() != proposals.resolve()):
        raise AutopilotError("proposal evidence directory is missing or unsafe")
    return run_dir


def _validate_gemini_chain(
    root: Path,
    source_head: str,
    result: dict,
    assignment_scope: AssignmentScope | None,
) -> dict:
    """Validate one exact Worker -> independent Gemini Reviewer evidence chain."""
    worker_dir = proposal_run_dir(root, result["gemini_worker_run_id"])
    reviewer_dir = proposal_run_dir(root, result["gemini_reviewer_run_id"])
    worker_result_path = worker_dir / "result.json"
    reviewer_result_path = reviewer_dir / "result.json"
    worker_result_text = bounded_file_text(
        root, worker_result_path, MAX_CONTEXT, "Gemini Worker result"
    )
    worker_snapshot = unique_json(bounded_file_text(
        root, worker_dir / "task.json", 2 * MAX_CONTEXT, "Gemini Worker snapshot"
    ))
    worker_status = unique_json(bounded_file_text(
        root, worker_dir / "status.json", MAX_CONTEXT, "Gemini Worker status"
    ))
    reviewer_snapshot = unique_json(bounded_file_text(
        root, reviewer_dir / "task.json", 2 * MAX_CONTEXT, "Gemini Reviewer snapshot"
    ))
    reviewer_result = unique_json(bounded_file_text(
        root, reviewer_result_path, MAX_CONTEXT, "Gemini Reviewer result"
    ))
    reviewer_status = unique_json(bounded_file_text(
        root, reviewer_dir / "status.json", MAX_CONTEXT, "Gemini Reviewer status"
    ))
    if (not isinstance(worker_snapshot, dict) or not isinstance(reviewer_snapshot, dict)
            or not isinstance(worker_status, dict) or not isinstance(reviewer_status, dict)):
        raise AutopilotError("Gemini evidence has an invalid shape")
    task = worker_snapshot.get("task")
    sources = worker_snapshot.get("sources")
    if not isinstance(task, dict) or not isinstance(sources, dict):
        raise AutopilotError("Gemini Worker snapshot is incomplete")
    if assignment_scope is not None:
        try:
            validate_task_scope(assignment_scope, task)
        except AssignmentScopeError:
            raise AutopilotError("assignment scope validation failed") from None
    worker_result = validate_proposal_result(
        unique_json(worker_result_text), task, sources, "worker"
    )
    if assignment_scope is not None:
        try:
            validate_changed_paths(
                assignment_scope,
                [change["path"] for change in worker_result["changes"]],
            )
        except AssignmentScopeError:
            raise AutopilotError("assignment scope validation failed") from None
    validate_proposal_result(reviewer_result, task, sources, "verifier")
    proposal_sha256 = file_sha256(worker_result_path)
    if (worker_snapshot.get("source_head") != source_head
            or reviewer_snapshot.get("source_head") != source_head
            or reviewer_snapshot.get("task") != task
            or reviewer_snapshot.get("sources") != sources
            or reviewer_snapshot.get("proposal") != worker_result_text
            or worker_status.get("run_id") != result["gemini_worker_run_id"]
            or worker_status.get("role") != "worker"
            or worker_status.get("state") != "PROPOSAL_READY"
            or worker_status.get("model") != GEMINI_WORKER_MODEL
            or worker_status.get("head") != source_head
            or worker_status.get("result_sha256") != proposal_sha256
            or reviewer_status.get("run_id") != result["gemini_reviewer_run_id"]
            or reviewer_status.get("role") != "verifier"
            or reviewer_status.get("state") != "VERIFIED"
            or reviewer_status.get("model") != GEMINI_MODEL
            or reviewer_status.get("head") != source_head
            or reviewer_status.get("input_worker_run_id") != result["gemini_worker_run_id"]
            or reviewer_status.get("input_proposal_sha256") != proposal_sha256
            or reviewer_status.get("result_sha256") != file_sha256(reviewer_result_path)
            or reviewer_result.get("status") != "VERIFIED"
            or result["proposal_sha256"] != proposal_sha256):
        raise AutopilotError("Gemini Worker/Reviewer evidence is not exactly bound")
    worker_conversation = worker_status.get("conversation_id")
    reviewer_conversation = reviewer_status.get("conversation_id")
    if (not isinstance(worker_conversation, str) or not worker_conversation.strip()
            or not isinstance(reviewer_conversation, str) or not reviewer_conversation.strip()
            or worker_conversation == reviewer_conversation):
        raise AutopilotError("Gemini review did not use an independent fresh session")
    return worker_result


def validate_gemini_chain(root: Path, source_head: str, result: dict) -> dict:
    """Validate one exact Worker -> independent Gemini Reviewer evidence chain."""
    return _validate_gemini_chain(root, source_head, result, None)


def validate_scoped_gemini_chain(
    root: Path,
    source_head: str,
    result: dict,
    assignment_scope: AssignmentScope,
) -> dict:
    if not isinstance(assignment_scope, AssignmentScope):
        raise AutopilotError("invalid or missing assignment scope")
    return _validate_gemini_chain(root, source_head, result, assignment_scope)


def git_blob(root: Path, commit: str, path: str) -> bytes:
    try:
        return subprocess.check_output(
            ["/usr/bin/git", "--no-replace-objects", "-C", str(root),
             "show", f"{commit}:{path}"],
            timeout=20, stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as exc:
        raise AutopilotError("committed proposal blob is missing") from exc


def tree_mode(root: Path, commit: str, path: str) -> str | None:
    record = git(root, "ls-tree", commit, "--", path)
    return record.split(maxsplit=1)[0] if record else None


def _validate_committed_proposal(
    root: Path,
    before_head: str,
    after_head: str,
    proposal: dict,
    assignment_scope: AssignmentScope | None,
) -> None:
    history = git(root, "rev-list", "--parents", "-n", "1", after_head).split()
    if len(history) != 2 or history[0] != after_head or history[1] != before_head:
        raise AutopilotError("cycle must create exactly one non-merge commit")
    changed_paths = set(git(
        root, "diff", "--name-only", "--no-renames", before_head, after_head,
    ).splitlines())
    proposal_paths = {change["path"] for change in proposal["changes"]}
    if assignment_scope is not None:
        try:
            validate_changed_paths(assignment_scope, sorted(changed_paths))
            validate_changed_paths(assignment_scope, sorted(proposal_paths))
        except AssignmentScopeError:
            raise AutopilotError("assignment scope validation failed") from None
    if changed_paths != proposal_paths:
        raise AutopilotError("commit paths differ from the Gemini proposal")
    for change in proposal["changes"]:
        path = change["path"]
        if git_blob(root, after_head, path) != change["content"].encode("utf-8"):
            raise AutopilotError("committed bytes differ from the Gemini proposal")
        before_mode = tree_mode(root, before_head, path)
        after_mode = tree_mode(root, after_head, path)
        if ((before_mode is None and after_mode != "100644")
                or (before_mode is not None and after_mode != before_mode)):
            raise AutopilotError("proposal commit contains an unreviewed mode change")


def validate_committed_proposal(
    root: Path, before_head: str, after_head: str, proposal: dict,
) -> None:
    _validate_committed_proposal(root, before_head, after_head, proposal, None)


def validate_scoped_committed_proposal(
    root: Path,
    before_head: str,
    after_head: str,
    proposal: dict,
    assignment_scope: AssignmentScope,
) -> None:
    if not isinstance(assignment_scope, AssignmentScope):
        raise AutopilotError("invalid or missing assignment scope")
    _validate_committed_proposal(root, before_head, after_head, proposal, assignment_scope)


def validate_transition(
    root: Path,
    before: dict[str, object],
    result: dict,
) -> dict[str, object]:
    after = validate_repository(root, require_clean=True)
    before_scope = before.get("assignment_scope")
    after_scope = after.get("assignment_scope")
    if (
        not isinstance(before_scope, AssignmentScope)
        or not isinstance(after_scope, AssignmentScope)
        or before_scope != after_scope
    ):
        raise AutopilotError("active assignment scope changed during cycle")
    if after["protected_refs"] != before["protected_refs"]:
        raise AutopilotError("a protected main/origin/candidate ref changed during the cycle")
    if after["control_blobs"] != before["control_blobs"]:
        raise AutopilotError("a protected automation control changed during the cycle")
    before_head = str(before["head"])
    after_head = str(after["head"])
    changed = before_head != after_head
    if changed:
        try:
            git(root, "merge-base", "--is-ancestor", before_head, after_head)
        except subprocess.CalledProcessError as exc:
            raise AutopilotError("cycle rewrote or diverged integration history") from exc
        if result["commit"] != after_head:
            raise AutopilotError("reported commit does not equal the final integration HEAD")
        proposal = validate_scoped_gemini_chain(root, before_head, result, before_scope)
        validate_scoped_committed_proposal(root, before_head, after_head, proposal, before_scope)
    elif result["commit"] is not None:
        raise AutopilotError("cycle reported a commit without changing integration HEAD")
    if result["state"] == "CONTINUE" and not changed:
        raise AutopilotError("CONTINUE without a clean commit is a no-progress loop")
    result = dict(result)
    result["head_before"] = before_head
    result["head_after"] = after_head
    result["head_changed"] = changed
    return result


def continuation_hint(result: object, current_head: object) -> dict | None:
    """Bounded optimization hint, never an acceptance/authority receipt."""
    if not isinstance(result, dict):
        return None
    keys = ("head_after", "commit", "summary", "next_action",
            "gemini_worker_run_id", "gemini_reviewer_run_id", "proposal_sha256")
    if (result.get("state") != "CONTINUE" or result.get("head_changed") is not True
            or result.get("blockers") != [] or result.get("head_after") != current_head
            or result.get("commit") != current_head
            or not all(isinstance(result.get(k), str) and result[k].strip() for k in keys)
            or not re.fullmatch(r"[0-9a-f]{40}", str(current_head))
            or not re.fullmatch(r"[0-9a-f]{64}", result["proposal_sha256"])
            or any(not PROPOSAL_RUN_ID.fullmatch(result[k]) for k in keys[4:6])):
        return None
    hint = {k: result[k] for k in keys}
    if len(json.dumps(hint, ensure_ascii=True).encode()) > 8000:
        return None
    return hint


def persisted_continuation(root: Path, state_root: Path, current_head: object) -> dict | None:
    """Recover only an exact accepted checkpoint from the prior supervisor state.

    This is an optimization hint, never an authority receipt.  It is deliberately
    ignored on any malformed, incomplete, stale, or non-CONTINUE state so a restart
    cannot turn a historical summary into permission to skip current inspection.
    """
    status_path = state_root / "status.json"
    try:
        state = unique_json(bounded_file_text(
            root, status_path, MAX_RESULT_BYTES, "persisted supervisor status"
        ))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(state, dict):
        return None
    candidate = {
        "state": state.get("state"),
        "head_changed": state.get("head_changed"),
        "head_after": state.get("head_after"),
        "commit": state.get("commit"),
        "blockers": state.get("blockers"),
        "summary": state.get("summary"),
        "next_action": state.get("next_action"),
        "gemini_worker_run_id": state.get("gemini_worker_run_id"),
        "gemini_reviewer_run_id": state.get("gemini_reviewer_run_id"),
        "proposal_sha256": state.get("proposal_sha256"),
    }
    return candidate if continuation_hint(candidate, current_head) is not None else None


def prompt_for_cycle(
    root: Path,
    cycle: int,
    before: dict[str, object],
    recovery: dict[str, object] | None = None,
    continuation: dict | None = None,
) -> str:
    if "assignment_scope" not in before:
        raise AutopilotError("missing assignment scope in repository state")
    scope_data = assignment_scope_context(before["assignment_scope"])
    base = bounded_file_text(
        root, root / PROMPT_REL, MAX_PROMPT_BYTES, "autopilot prompt template"
    )
    context: dict[str, object] = {
        "cycle": cycle,
        "starting_head": before["head"],
        "protected_refs": before["protected_refs"],
        "assignment_scope": scope_data,
        "startup_validation": True,
    }
    if recovery is not None:
        context["recovery"] = recovery
    hint = continuation_hint(continuation, before["head"])
    if hint is not None:
        context["validated_previous_cycle_hint"] = hint
    return base + "\n\nSUPERVISOR_CONTEXT_JSON:\n" + json.dumps(context, sort_keys=True)


def codex_command(codex: Path, root: Path, run_dir: Path, prompt: str) -> list[str]:
    return [
        str(codex), "exec",
        "--model", MODEL,
        "--config", f'model_reasoning_effort="{REASONING}"',
        "--enable", "code_mode",
        "--cd", str(root),
        "--approve-for-me",
        "--ephemeral",
        "--output-schema", str(root / SCHEMA_REL),
        "--output-last-message", str(run_dir / "result.json"),
        "--json",
        prompt,
    ]


def run_cycle(
    root: Path,
    codex: Path,
    state_root: Path,
    cycle: int,
    timeout: int,
    recovery: dict[str, object] | None = None,
    continuation: dict | None = None,
) -> tuple[Path, dict]:
    global _ACTIVE_PROCESS
    heartbeat_stop: threading.Event | None = None
    heartbeat_thread: threading.Thread | None = None
    before = validate_repository(root, require_clean=True)
    scope = before.get("assignment_scope")
    if not isinstance(scope, AssignmentScope):
        raise AutopilotError("invalid or missing startup assignment scope")
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = state_root / "runs" / f"cycle-{cycle:04d}-{timestamp}-{os.getpid()}"
    if not RUN_ID.fullmatch(run_dir.name) or os.path.isabs(run_dir.name):
        raise AutopilotError(f"invalid cycle run ID: {run_dir.name}")
    run_dir.mkdir(mode=0o700)
    prompt = prompt_for_cycle(root, cycle, before, recovery=recovery, continuation=continuation)
    (run_dir / "prompt.txt").write_text(prompt)
    codex = codex.resolve(strict=True)
    identity = codex_identity(codex, run_dir)
    meta = {
        "cycle": cycle,
        "state": "RUNNING",
        "started_at": time.time(),
        "phase": "COORDINATOR",
        "phase_started_at": time.time(),
        "heartbeat_at": time.time(),
        "head_before": before["head"],
        "protected_refs": before["protected_refs"],
        "codex": identity,
        "assignment_scope": {
            "assignment_id": scope.assignment_id,
            "stage_id": scope.stage_id,
            "sha256": scope.sha256,
        },
        "output_limit_bytes": MAX_CODEX_OUTPUT,
        "timeout_seconds": timeout,
    }
    if recovery is not None:
        meta["recovery"] = recovery
    write_json(run_dir / "status.json", meta)
    command = codex_command(codex, root, run_dir, prompt)
    environment = os.environ.copy()
    environment["APP_ENV_FILE"] = "config/unit-test.env.example"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED"] = "1"
    environment["TWO_SITE_ASSIGNMENT_ID"] = scope.assignment_id
    environment["TWO_SITE_ASSIGNMENT_SHA256"] = scope.sha256
    environment["TWO_SITE_AUTOPILOT_RUN_ID"] = run_dir.name
    try:
        with subprocess.Popen(
            command,
            cwd=root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        ) as process:
            _ACTIVE_PROCESS = process
            meta["pid"] = process.pid
            write_json(run_dir / "status.json", meta)
            phase_started_at = time.time()
            heartbeat_stop = threading.Event()
            write_supervisor_state(
                state_root, "RUNNING", announce=False, cycle=cycle,
                head=before["head"], phase="COORDINATOR",
                phase_started_at=phase_started_at, heartbeat_at=phase_started_at,
                summary="Executing Gemini Worker -> Gemini Reviewer -> Codex final gate.",
                blockers=[],
            )
            heartbeat_thread = threading.Thread(
                target=_heartbeat_running_cycle,
                args=(heartbeat_stop, state_root, cycle, before["head"], phase_started_at),
                name="two-site-supervisor-heartbeat",
                daemon=True,
            )
            heartbeat_thread.start()
            capture_bounded_process(
                process, b"", run_dir / "stream.jsonl", run_dir / "stderr.txt",
                timeout, max_output=MAX_CODEX_OUTPUT,
            )
        if process.returncode:
            raise AutopilotError(f"Codex CLI exited with status {process.returncode}")
        if file_sha256(codex) != identity["sha256"]:
            raise AutopilotError("Codex CLI identity changed during the cycle")
        if codex_runtime_identity(codex) != identity["runtime"]:
            raise AutopilotError("Codex runtime changed during the cycle")
        result_path = run_dir / "result.json"
        raw_result = unique_json(
            bounded_file_text(root, result_path, MAX_RESULT_BYTES, "Codex cycle result")
        )
        try:
            validated_receipt = validate_result(raw_result)
            advisories: list[str] = []
        except AutopilotError as exc:
            if str(exc) != "non-blocked state cannot report blockers":
                raise
            validated_receipt, advisories = recover_committed_advisory_result(raw_result, exc)
        result = validate_transition(root, before, validated_receipt)
        result["advisories"] = advisories
        meta.update(
            state=result["state"],
            finished_at=time.time(),
            duration_seconds=round(time.time() - float(meta["started_at"]), 3),
            head_after=result["head_after"],
            summary=result["summary"],
            next_action=result["next_action"],
            blockers=result["blockers"],
            advisories=result["advisories"],
            gemini_worker_run_id=result["gemini_worker_run_id"],
            gemini_reviewer_run_id=result["gemini_reviewer_run_id"],
            proposal_sha256=result["proposal_sha256"],
        )
        write_json(run_dir / "status.json", meta)
        return run_dir, result
    except BaseException as exc:
        meta.update(
            state="FAILED",
            error=type(exc).__name__ + ": " + str(exc),
            finished_at=time.time(),
            duration_seconds=round(time.time() - float(meta["started_at"]), 3),
        )
        write_json(run_dir / "status.json", meta)
        raise
    finally:
        if heartbeat_stop is not None:
            heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join()
        _ACTIVE_PROCESS = None
        # Telemetry must neither authorize nor block a valid transition. Persist
        # before run pruning, including failed/interrupted attempts.
        try:
            try:
                from scripts.two_site_cycle_metrics import record_cycle
            except ModuleNotFoundError:
                from two_site_cycle_metrics import record_cycle
            record_cycle(root, state_root, run_dir, meta, MODEL, REASONING)
        except Exception as exc:
            meta["metrics_error"] = type(exc).__name__
            try:
                write_json(run_dir / "status.json", meta)
            except OSError:
                pass


def prune_runs(state_root: Path, keep: int = KEEP_RUNS) -> list[str]:
    runs = state_root / "runs"
    if not runs.exists():
        return []
    candidates = sorted(
        (path for path in runs.iterdir() if path.is_dir() and RUN_ID.fullmatch(path.name)),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    removed = []
    for path in candidates[keep:]:
        if path.resolve().parent != runs.resolve():
            raise AutopilotError("run retention path is unsafe")
        status_path = path / "status.json"
        try:
            status = unique_json(bounded_file_text(
                state_root.resolve(), status_path, MAX_RESULT_BYTES, "retention status"
            ))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(status, dict) or status.get("state") not in PRUNABLE_RUN_STATES:
            continue
        shutil.rmtree(path)
        removed.append(path.name)
    return removed


def write_supervisor_state(
    state_root: Path, state: str, *, announce: bool = True, **fields: object
) -> None:
    payload = {"state": state, "updated_at": time.time(), **fields}
    write_json(state_root / "status.json", payload)
    if announce:
        print(json.dumps(payload, ensure_ascii=False), flush=True)


def _heartbeat_running_cycle(
    stopped: threading.Event,
    state_root: Path,
    cycle: int,
    head: object,
    phase_started_at: float,
    *,
    interval: float = SUPERVISOR_HEARTBEAT_SECONDS,
) -> None:
    """Refresh bounded, source-free liveness evidence while Codex is running.

    Heartbeats are observability only: an I/O failure cannot accept, retry, stop,
    or otherwise alter a cycle.  The monitor may report stale evidence, but never
    terminates a live supervisor merely because a heartbeat is missing.
    """
    while not stopped.wait(interval):
        try:
            now = time.time()
            write_supervisor_state(
                state_root, "RUNNING", announce=False, cycle=cycle, head=head,
                phase="COORDINATOR", phase_started_at=phase_started_at,
                heartbeat_at=now,
                summary="Executing Gemini Worker -> Gemini Reviewer -> Codex final gate.",
                blockers=[],
            )
        except OSError:
            pass


def signal_handler(_signum: int, _frame: object) -> None:
    if _ACTIVE_PROCESS is not None:
        terminate_process(_ACTIVE_PROCESS)
    raise KeyboardInterrupt("autopilot stopped by signal")


def run_loop(root: Path, codex: Path, max_cycles: int, timeout: int, once: bool) -> int:
    root = root.resolve(strict=True)
    state_root = root / ".local/two-site-refactor/autopilot"
    if state_root.resolve() != state_root:
        raise AutopilotError("autopilot state directory cannot be a symlink")
    (state_root / "runs").mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = state_root / "autopilot.lock"
    with exclusive_supervisor_lock(lock_path):
        retryable_failures = 0
        consecutive_rescopes = 0
        recovery_context: dict[str, object] | None = None
        previous_success: dict | None = None
        for cycle in range(1, max_cycles + 1):
            if (state_root / "STOP").exists():
                write_supervisor_state(state_root, "STOPPED", cycle=cycle)
                return 0
            cycle_start_head = git(root, "rev-parse", "HEAD")
            if cycle == 1:
                previous_success = persisted_continuation(root, state_root, cycle_start_head)
            cycle_start_refs = ref_snapshot(root)
            phase_started_at = time.time()
            write_supervisor_state(
                state_root, "RUNNING", cycle=cycle, head=cycle_start_head,
                phase="PREPARING", phase_started_at=phase_started_at,
                heartbeat_at=phase_started_at,
                summary="Executing Gemini Worker -> Gemini Reviewer -> Codex final gate.",
                blockers=[],
            )
            try:
                continuation_args = ({"continuation": previous_success}
                                     if continuation_hint(previous_success, cycle_start_head) is not None else {})
                run_dir, result = run_cycle(
                    root, codex, state_root, cycle, timeout, recovery=recovery_context,
                    **continuation_args,
                )
                previous_success = None
                retryable_failures = 0
                if result.get("head_changed") and result.get("commit") is not None:
                    consecutive_rescopes = 0
                    recovery_context = None
                if result["state"] == "COMPLETE":
                    consecutive_rescopes = 0
                    recovery_context = None
                    prune_runs(state_root)
                    write_supervisor_state(
                        state_root, "COMPLETE", cycle=cycle,
                        evidence=str(run_dir), head=result["head_after"],
                        summary=result["summary"], next_action=result["next_action"],
                        blockers=result["blockers"],
                        gemini_worker_run_id=result["gemini_worker_run_id"],
                        gemini_reviewer_run_id=result["gemini_reviewer_run_id"],
                        proposal_sha256=result["proposal_sha256"],
                    )
                    return 0
                if result["state"] == "CONTINUE":
                    if continuation_hint(result, result.get("head_after")) is not None:
                        previous_success = result
                    consecutive_rescopes = 0
                    recovery_context = None
                    prune_runs(state_root)
                    write_supervisor_state(
                        state_root, "CONTINUE", cycle=cycle,
                        evidence=str(run_dir), head=result["head_after"],
                        summary=result["summary"], next_action=result["next_action"],
                        blockers=result["blockers"],
                        advisories=result.get("advisories", []),
                        head_after=result["head_after"],
                        head_changed=result["head_changed"],
                        commit=result["commit"],
                        gemini_worker_run_id=result["gemini_worker_run_id"],
                        gemini_reviewer_run_id=result["gemini_reviewer_run_id"],
                        proposal_sha256=result["proposal_sha256"],
                    )
                    if once:
                        return 2
                    continue

                is_rescope = (
                    result.get("disposition") == "RESCOPE"
                    and result.get("category") == "INTERNAL_REJECTION"
                    and not result.get("head_changed")
                    and result.get("commit") is None
                    and result.get("gemini_worker_run_id") is None
                    and result.get("gemini_reviewer_run_id") is None
                    and result.get("proposal_sha256") is None
                )
                if is_rescope:
                    consecutive_rescopes += 1
                    if consecutive_rescopes >= MAX_CONSECUTIVE_RESCOPES:
                        prune_runs(state_root)
                        write_supervisor_state(
                            state_root, "BLOCKED", cycle=cycle,
                            evidence=str(run_dir), head=result["head_after"],
                            summary=result["summary"], next_action=result["next_action"],
                            blockers=result["blockers"],
                            gemini_worker_run_id=None,
                            gemini_reviewer_run_id=None,
                            proposal_sha256=None,
                        )
                        return 2
                    prune_runs(state_root)
                    write_supervisor_state(
                        state_root, "RESCOPE", cycle=cycle,
                        evidence=str(run_dir), head=result["head_after"],
                        summary=result["summary"], next_action=result["next_action"],
                        blockers=result["blockers"],
                        gemini_worker_run_id=None,
                        gemini_reviewer_run_id=None,
                        proposal_sha256=None,
                    )
                    if once:
                        return 2
                    recovery_context = {
                        "attempt": consecutive_rescopes,
                        "attempt_count": consecutive_rescopes,
                        "consecutive_rescopes": consecutive_rescopes,
                        "prior_unit_rejected": True,
                        "rejected": True,
                        "next_action": result["next_action"],
                        "blockers": list(result["blockers"]),
                        "defer_prior_unit": consecutive_rescopes >= 2,
                    }
                    if recovery_context["defer_prior_unit"]:
                        recovery_context["deferred_unit"] = {
                            "next_action": result["next_action"],
                            "blockers": list(result["blockers"]),
                        }
                    continue

                prune_runs(state_root)
                write_supervisor_state(
                    state_root, "BLOCKED", cycle=cycle,
                    evidence=str(run_dir), head=result["head_after"],
                    summary=result["summary"], next_action=result["next_action"],
                    blockers=result["blockers"],
                    gemini_worker_run_id=result["gemini_worker_run_id"],
                    gemini_reviewer_run_id=result["gemini_reviewer_run_id"],
                    proposal_sha256=result["proposal_sha256"],
                )
                return 2
            except Exception as exc:
                previous_success = None
                # Never leave the supervisor appearing RUNNING after an
                # unexpected application error. KeyboardInterrupt/SystemExit
                # remain outside Exception and still stop immediately.
                current_head = git(root, "rev-parse", "HEAD")
                dirty = bool(git(root, "status", "--porcelain=v1", "--untracked-files=all"))
                ambiguous_change = current_head != cycle_start_head or ref_snapshot(root) != cycle_start_refs
                retryable_failures += 1
                if dirty or ambiguous_change or retryable_failures > MAX_RETRYABLE_FAILURES:
                    write_supervisor_state(
                        state_root, "BLOCKED", cycle=cycle, head=current_head,
                        blockers=[type(exc).__name__ + ": " + str(exc)], dirty=dirty,
                        ambiguous_change=ambiguous_change,
                    )
                    return 2
                write_supervisor_state(
                    state_root, "RETRYABLE_FAILURE", cycle=cycle, head=current_head,
                    retry=retryable_failures,
                    error=type(exc).__name__ + ": " + str(exc),
                )
                if once:
                    return 2
                time.sleep(15 * retryable_failures)
        write_supervisor_state(
            state_root, "BLOCKED", cycle=max_cycles,
            blockers=["bounded maximum cycle count reached"],
        )
        return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--codex", type=Path, default=None)
    parser.add_argument("--max-cycles", type=int, default=DEFAULT_MAX_CYCLES)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.codex is None:
        args.codex = args.root.resolve() / ".local/tools/codex-0.153.4-56ef98ab4032d317.pin"
    if not 1 <= args.max_cycles <= DEFAULT_MAX_CYCLES or not 300 <= args.timeout <= 14_400:
        raise SystemExit("invalid max-cycles or timeout")
    for handled in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(handled, signal_handler)
    if args.check:
        state = validate_repository(args.root)
        with tempfile.TemporaryDirectory(prefix="two-site-autopilot-check-") as temporary:
            identity = codex_identity(args.codex, Path(temporary))
        print(json.dumps({
            "state": "READY", "branch": BRANCH, "head": state["head"],
            "codex": identity,
            "assignment_scope": assignment_scope_context(state.get("assignment_scope")),
        }))
        return 0
    try:
        return run_loop(args.root, args.codex, args.max_cycles, args.timeout, args.once)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"state": "BLOCKED", "reason": str(exc)}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
