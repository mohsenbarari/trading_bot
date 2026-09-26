#!/usr/bin/env python3
"""Run bounded non-interactive Codex cycles until completion or a real blocker."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
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
import sys
import tempfile
import threading
import time
from types import MappingProxyType
from typing import Callable

try:
    from scripts.two_site_proposal_runner import (
        MAX_CONTEXT,
        GEMINI_RUNTIME_CONTRACT_REL,
        MODEL as GEMINI_MODEL,
        ProposalError,
        bounded_file_text,
        capture_bounded_process,
        file_sha256,
        file_fingerprint,
        git,
        serialize_json,
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
        ProposalError,
        bounded_file_text,
        capture_bounded_process,
        file_sha256,
        file_fingerprint,
        git,
        serialize_json,
        terminate_process,
        unique_json,
        validate_runtime_contract,
        validate_result as validate_proposal_result,
        write_json,
    )
try:
    from scripts.two_site_proposal_pipeline import expected_role_model
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    from two_site_proposal_pipeline import expected_role_model  # type: ignore[no-redef]
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
try:
    from scripts.two_site_preparation_contract import (
        PreparationConfigError,
        PreparationStartup,
        assert_same_startup,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    from two_site_preparation_contract import (  # type: ignore[no-redef]
        PreparationConfigError,
        PreparationStartup,
        assert_same_startup,
    )
try:
    from scripts.two_site_provider_broker import BrokerError, _atomic_new_json
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    from two_site_provider_broker import BrokerError, _atomic_new_json  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parents[1]
BRANCH = "refactor/two-site-architecture"
MODEL = "gpt-6-sol"
REASONING = "xhigh"
EXPECTED_CODEX_CLI_VERSION = "codex-cli 0.156.1"
EXPECTED_CODEX_CLI_SHA256 = "0b2e9301d6100dddda3b9d5c80ebaeaa3a2f1962388f2f36f6b96a9f08b1f33f"
CODEX_RUNTIME_FILES = {
    "codex-code-mode-host": "45ba654b0e145406f3316be4729b886980ce811aa2e3b6ed4e033dd41ab524f1",
    "codex-package.json": "6cf97f0c8999775e81c038429a31f28c406e077ecdbece52a9700e244bd2458f",
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
MAX_ACCEPTED_CHECKPOINT_BYTES = 16 * 1024
ACCEPTED_CHECKPOINT_SCHEMA_VERSION = 1
_ACCEPTED_CHECKPOINT_FILENAME = "accepted_checkpoint.json"
ACCEPTED_CHECKPOINT_FILENAME = _ACCEPTED_CHECKPOINT_FILENAME
ACCEPTED_RECEIPT_EXTRA_KEYS = ("head_before", "head_after", "head_changed")
ACCEPTED_RECEIPT_KEYS = frozenset(RESULT_KEYS | set(ACCEPTED_RECEIPT_EXTRA_KEYS))
ACCEPTED_RESULT_KEYS_ORDER = (
    "schema_version",
    "state",
    "disposition",
    "category",
    "summary",
    "commit",
    "next_action",
    "blockers",
    "gemini_worker_run_id",
    "gemini_reviewer_run_id",
    "proposal_sha256",
    "head_before",
    "head_after",
    "head_changed",
)
ACCEPTED_REPOSITORY_KEYS_ORDER = (
    "head",
    "status",
    "protected_refs",
    "control_blobs",
    "assignment_scope",
)
ASSIGNMENT_SCOPE_KEYS_ORDER = (
    "assignment_id",
    "stage_id",
    "sha256",
    "expires_at",
    "allowed_paths",
    "forbidden_paths",
)
REPOSITORY_FACT_KEYS = frozenset({
    "head", "status", "protected_refs", "control_blobs", "assignment_scope",
})
MAX_RESCOPE_RECEIPT_BYTES = 16 * 1024
RESCOPE_RECEIPT_SCHEMA_VERSION = 1
_RESCOPE_RECEIPT_FILENAME = "rescope_receipt.json"
_RESCOPE_RECEIPT_FILENAMES = MappingProxyType({
    "rescope_receipt.json": 1,
    "rescope_receipt_2.json": 2,
    "rescope_receipt_3.json": 3,
})
RESCOPE_RECEIPT_FILENAME = _RESCOPE_RECEIPT_FILENAME
_RESCOPE_INTENT_FILENAMES = MappingProxyType({
    1: "rescope_intent_1",
    2: "rescope_intent_2",
    3: "rescope_intent_3",
})
_RESCOPE_INTENT_LEAF_NAMES = _RESCOPE_INTENT_FILENAMES
RESCOPE_INTENT_FILENAMES = _RESCOPE_INTENT_FILENAMES
RESCOPE_RECEIPT_ENVELOPE_KEYS = frozenset({
    "schema_version",
    "attempt",
    "terminal",
    "result",
    "repository",
})
RESCOPE_RECEIPT_KEYS = ACCEPTED_RECEIPT_KEYS
RESCOPE_RESULT_KEYS_ORDER = ACCEPTED_RESULT_KEYS_ORDER
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
    Path("scripts/two_site_preparation_contract.py"),
    Path("scripts/two_site_preparation_io.py"),
    Path("scripts/two_site_preparation_boundary.py"),
    Path("scripts/two_site_preparation_runtime_gate.py"),
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


def validate_accepted_repository(repository: object) -> dict[str, object]:
    """Validate and normalize repository facts for an accepted checkpoint."""
    if isinstance(repository, Path):
        raise AutopilotError("repository facts cannot be a Path")
    if not isinstance(repository, dict) or set(repository) != REPOSITORY_FACT_KEYS:
        raise AutopilotError("repository facts do not match the exact canonical shape")
    head = repository.get("head")
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise AutopilotError("repository head must be a lowercase 40-hex SHA")
    if repository.get("status") != "":
        raise AutopilotError("repository status must be the empty clean-tree string")
    protected_refs = repository.get("protected_refs")
    if not isinstance(protected_refs, dict) or set(protected_refs) != set(PROTECTED_REFS):
        raise AutopilotError("protected_refs must contain every and only PROTECTED_REFS key")
    for ref_name, ref_sha in protected_refs.items():
        if not isinstance(ref_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", ref_sha):
            raise AutopilotError(f"protected ref {ref_name} identity must be a lowercase 40-hex SHA")
    control_blobs = repository.get("control_blobs")
    expected_controls = {p.as_posix() for p in CONTROL_PATHS}
    if not isinstance(control_blobs, dict) or set(control_blobs) != expected_controls:
        raise AutopilotError("control_blobs must contain every and only CONTROL_PATHS key")
    for path_key, blob_sha in control_blobs.items():
        if not isinstance(blob_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", blob_sha):
            raise AutopilotError(f"control blob {path_key} identity must be a lowercase 40-hex SHA")
    scope = repository.get("assignment_scope")
    if not isinstance(scope, AssignmentScope):
        raise AutopilotError("assignment_scope must be an AssignmentScope dataclass instance")
    scope_data = assignment_scope_context(scope)
    if set(scope_data) != set(ASSIGNMENT_SCOPE_KEYS_ORDER):
        raise AutopilotError("assignment_scope must materialize with its complete six-key identity")
    for str_key in ("assignment_id", "stage_id", "sha256", "expires_at"):
        val = scope_data.get(str_key)
        if not isinstance(val, str) or not val.strip():
            raise AutopilotError(f"assignment_scope {str_key} must be a non-empty string")
    for list_key in ("allowed_paths", "forbidden_paths"):
        val = scope_data.get(list_key)
        if not isinstance(val, list) or any(not isinstance(p, str) for p in val):
            raise AutopilotError(f"assignment_scope {list_key} must be a list of strings")

    normalized_scope = {key: scope_data[key] for key in ASSIGNMENT_SCOPE_KEYS_ORDER}
    normalized_protected_refs = {ref: protected_refs[ref] for ref in sorted(PROTECTED_REFS)}
    normalized_control_blobs = {path: control_blobs[path] for path in sorted(expected_controls)}

    return {
        "head": head,
        "status": "",
        "protected_refs": normalized_protected_refs,
        "control_blobs": normalized_control_blobs,
        "assignment_scope": normalized_scope,
    }


def validate_accepted_receipt(result: object, repo_head: str) -> dict[str, object]:
    """Validate and normalize a CONTINUE transition receipt for an accepted checkpoint."""
    if isinstance(result, Path):
        raise AutopilotError("receipt cannot be a Path")
    if not isinstance(result, dict) or set(result) != ACCEPTED_RECEIPT_KEYS:
        raise AutopilotError("receipt keys must exactly match RESULT_KEYS plus head_before, head_after, and head_changed")
    projection = {k: result[k] for k in RESULT_KEYS}
    validate_result(projection)
    if result.get("state") != "CONTINUE":
        raise AutopilotError("receipt state must be CONTINUE")
    if result.get("disposition") != "NONE":
        raise AutopilotError("receipt disposition must be NONE")
    if result.get("category") != "NONE":
        raise AutopilotError("receipt category must be NONE")
    if result.get("blockers") != []:
        raise AutopilotError("receipt blockers must be empty list")
    if result.get("head_changed") is not True:
        raise AutopilotError("receipt head_changed must be True")
    head_before = result.get("head_before")
    head_after = result.get("head_after")
    if not isinstance(head_before, str) or not re.fullmatch(r"[0-9a-f]{40}", head_before):
        raise AutopilotError("head_before must be a lowercase 40-hex SHA")
    if not isinstance(head_after, str) or not re.fullmatch(r"[0-9a-f]{40}", head_after):
        raise AutopilotError("head_after must be a lowercase 40-hex SHA")
    if head_before == head_after:
        raise AutopilotError("head_before and head_after must be distinct lowercase 40-hex SHAs")
    commit = result.get("commit")
    if commit != head_after:
        raise AutopilotError("commit must equal head_after")
    if head_after != repo_head:
        raise AutopilotError("commit and head_after must equal repository head")

    return {k: result[k] for k in ACCEPTED_RESULT_KEYS_ORDER}


def validate_accepted_checkpoint(
    result: object,
    repository: object,
) -> tuple[dict[str, object], dict[str, object]]:
    """Validate both the CONTINUE receipt and repository facts, returning normalized mappings."""
    if isinstance(result, Path) or isinstance(repository, Path):
        raise AutopilotError("checkpoint arguments cannot be Path instances")
    normalized_repo = validate_accepted_repository(repository)
    normalized_result = validate_accepted_receipt(result, str(normalized_repo["head"]))
    return normalized_result, normalized_repo


def serialize_accepted_checkpoint(
    result: object,
    repository: object,
) -> bytes:
    """Construct and serialize a canonical accepted checkpoint envelope into deterministic UTF-8 bytes."""
    normalized_result, normalized_repo = validate_accepted_checkpoint(result, repository)
    envelope = {
        "schema_version": ACCEPTED_CHECKPOINT_SCHEMA_VERSION,
        "result": normalized_result,
        "repository": normalized_repo,
    }
    encoded = serialize_json(envelope)
    if len(encoded) > MAX_ACCEPTED_CHECKPOINT_BYTES:
        raise AutopilotError(
            f"serialized accepted checkpoint exceeds {MAX_ACCEPTED_CHECKPOINT_BYTES} bytes ({len(encoded)} bytes)"
        )
    return encoded


def _persist_accepted_checkpoint(
    state_root: Path,
    result: object,
    repository: object,
) -> None:
    """Atomically persist canonical accepted checkpoint bytes under state_root."""
    payload = serialize_accepted_checkpoint(result, repository)
    state_path = Path(state_root)
    canonical_path = state_path / _ACCEPTED_CHECKPOINT_FILENAME

    fd, temp_str = tempfile.mkstemp(
        dir=state_path,
        prefix=".accepted_checkpoint-",
        suffix=".tmp",
    )
    temp_path = Path(temp_str)
    fd_closed = False
    replaced = False
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd_closed = True
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, canonical_path)
        replaced = True
    finally:
        if not fd_closed:
            try:
                os.close(fd)
            except OSError:
                pass
        if not replaced:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def publish_accepted_checkpoint(
    state_root: Path,
    result: object,
    repository: object,
) -> None:
    """Revalidate and atomically persist an accepted checkpoint for a committed CONTINUE cycle."""
    if not isinstance(state_root, Path):
        raise AutopilotError("state_root must be a Path")
    validate_accepted_checkpoint(result, repository)
    _persist_accepted_checkpoint(state_root, result, repository)


def _validate_persisted_repository(repository: object) -> dict[str, object]:
    """Validate and normalize persisted repository facts from an accepted checkpoint."""
    if isinstance(repository, Path):
        raise AutopilotError("repository facts cannot be a Path")
    if not isinstance(repository, dict) or set(repository) != REPOSITORY_FACT_KEYS:
        raise AutopilotError("repository facts do not match the exact canonical shape")
    head = repository.get("head")
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise AutopilotError("repository head must be a lowercase 40-hex SHA")
    if repository.get("status") != "":
        raise AutopilotError("repository status must be the empty clean-tree string")
    protected_refs = repository.get("protected_refs")
    if not isinstance(protected_refs, dict) or set(protected_refs) != set(PROTECTED_REFS):
        raise AutopilotError("protected_refs must contain every and only PROTECTED_REFS key")
    for ref_name, ref_sha in protected_refs.items():
        if not isinstance(ref_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", ref_sha):
            raise AutopilotError(f"protected ref {ref_name} identity must be a lowercase 40-hex SHA")
    control_blobs = repository.get("control_blobs")
    expected_controls = {p.as_posix() for p in CONTROL_PATHS}
    if not isinstance(control_blobs, dict) or set(control_blobs) != expected_controls:
        raise AutopilotError("control_blobs must contain every and only CONTROL_PATHS key")
    for path_key, blob_sha in control_blobs.items():
        if not isinstance(blob_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", blob_sha):
            raise AutopilotError(f"control blob {path_key} identity must be a lowercase 40-hex SHA")
    scope = repository.get("assignment_scope")
    if not isinstance(scope, dict) or isinstance(scope, AssignmentScope):
        raise AutopilotError("assignment_scope must be a serialized mapping, not AssignmentScope")
    if set(scope) != set(ASSIGNMENT_SCOPE_KEYS_ORDER):
        raise AutopilotError("assignment_scope must contain every and only ASSIGNMENT_SCOPE_KEYS_ORDER key")
    for str_key in ("assignment_id", "stage_id", "sha256", "expires_at"):
        val = scope.get(str_key)
        if not isinstance(val, str) or not val.strip():
            raise AutopilotError(f"assignment_scope {str_key} must be a non-empty string")
    for list_key in ("allowed_paths", "forbidden_paths"):
        val = scope.get(list_key)
        if not isinstance(val, list) or any(not isinstance(p, str) for p in val):
            raise AutopilotError(f"assignment_scope {list_key} must be a list of strings")

    normalized_scope = {key: scope[key] for key in ASSIGNMENT_SCOPE_KEYS_ORDER}
    normalized_protected_refs = {ref: protected_refs[ref] for ref in sorted(PROTECTED_REFS)}
    normalized_control_blobs = {path: control_blobs[path] for path in sorted(expected_controls)}

    return {
        "head": head,
        "status": "",
        "protected_refs": normalized_protected_refs,
        "control_blobs": normalized_control_blobs,
        "assignment_scope": normalized_scope,
    }


def _load_accepted_checkpoint(state_root: Path) -> dict[str, object] | None:
    """Read and validate the canonical accepted checkpoint under state_root.

    Fails closed by returning None on any missing, malformed, oversized, linked,
    or schema-mismatched file.
    """
    try:
        state_path = Path(state_root)
        resolved_root = state_path.resolve(strict=True)
        canonical_path = resolved_root / _ACCEPTED_CHECKPOINT_FILENAME

        if canonical_path.parent != resolved_root or canonical_path.name != _ACCEPTED_CHECKPOINT_FILENAME:
            return None

        st = canonical_path.stat(follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            return None

        raw_text = bounded_file_text(
            resolved_root,
            canonical_path,
            MAX_ACCEPTED_CHECKPOINT_BYTES,
            "accepted checkpoint",
        )
        raw_bytes = raw_text.encode("utf-8")
        if len(raw_bytes) > MAX_ACCEPTED_CHECKPOINT_BYTES:
            return None

        parsed = unique_json(raw_text)
        if not isinstance(parsed, dict) or set(parsed) != {"schema_version", "result", "repository"}:
            return None

        schema_version = parsed["schema_version"]
        if type(schema_version) is not int or schema_version != ACCEPTED_CHECKPOINT_SCHEMA_VERSION:
            return None

        normalized_repo = _validate_persisted_repository(parsed["repository"])
        repo_head = str(normalized_repo["head"])
        normalized_result = validate_accepted_receipt(parsed["result"], repo_head)

        envelope = {
            "schema_version": ACCEPTED_CHECKPOINT_SCHEMA_VERSION,
            "result": normalized_result,
            "repository": normalized_repo,
        }
        canonical_bytes = serialize_json(envelope)
        if canonical_bytes != raw_bytes:
            return None

        return envelope
    except Exception:
        return None


def _accepted_checkpoint_matches_repository(
    checkpoint: object,
    repository: object,
) -> bool:
    """Compare an accepted checkpoint envelope against live repository facts.

    Fail-closed, side-effect-free comparator returning True only when the
    checkpoint envelope is valid and normalized repository facts match exactly.
    """
    try:
        if not isinstance(checkpoint, dict) or set(checkpoint) != {"schema_version", "result", "repository"}:
            return False
        schema_version = checkpoint.get("schema_version")
        if type(schema_version) is not int or schema_version != ACCEPTED_CHECKPOINT_SCHEMA_VERSION:
            return False
        persisted_repo = _validate_persisted_repository(checkpoint.get("repository"))
        repo_head = str(persisted_repo["head"])
        validate_accepted_receipt(checkpoint.get("result"), repo_head)
        current_repo = validate_accepted_repository(repository)
        return persisted_repo == current_repo
    except Exception:
        return False


def validate_rescope_receipt(result: object, repo_head: str) -> dict[str, object]:
    """Validate and normalize a BLOCKED/RESCOPE transition receipt."""
    if isinstance(result, Path):
        raise AutopilotError("receipt cannot be a Path")
    if not isinstance(result, dict) or set(result) != RESCOPE_RECEIPT_KEYS:
        raise AutopilotError("receipt keys must exactly match RESULT_KEYS plus head_before, head_after, and head_changed")
    projection = {k: result[k] for k in RESULT_KEYS}
    validate_result(projection)
    if result.get("state") != "BLOCKED":
        raise AutopilotError("receipt state must be BLOCKED")
    if result.get("disposition") != "RESCOPE":
        raise AutopilotError("receipt disposition must be RESCOPE")
    if result.get("category") != "INTERNAL_REJECTION":
        raise AutopilotError("receipt category must be INTERNAL_REJECTION")
    if result.get("commit") is not None:
        raise AutopilotError("receipt commit must be None")
    if result.get("gemini_worker_run_id") is not None:
        raise AutopilotError("receipt gemini_worker_run_id must be None")
    if result.get("gemini_reviewer_run_id") is not None:
        raise AutopilotError("receipt gemini_reviewer_run_id must be None")
    if result.get("proposal_sha256") is not None:
        raise AutopilotError("receipt proposal_sha256 must be None")
    if result.get("head_changed") is not False or type(result.get("head_changed")) is not bool:
        raise AutopilotError("receipt head_changed must be False")
    head_before = result.get("head_before")
    head_after = result.get("head_after")
    if not isinstance(head_before, str) or not re.fullmatch(r"[0-9a-f]{40}", head_before):
        raise AutopilotError("head_before must be a lowercase 40-hex SHA")
    if not isinstance(head_after, str) or not re.fullmatch(r"[0-9a-f]{40}", head_after):
        raise AutopilotError("head_after must be a lowercase 40-hex SHA")
    if head_before != head_after:
        raise AutopilotError("head_before and head_after must be equal lowercase 40-hex SHAs")
    if head_after != repo_head:
        raise AutopilotError("head_before and head_after must equal repository head")

    return {k: result[k] for k in RESCOPE_RESULT_KEYS_ORDER}


def validate_rescope_checkpoint(
    result: object,
    repository: object,
) -> tuple[dict[str, object], dict[str, object]]:
    """Validate both the BLOCKED/RESCOPE receipt and repository facts, returning normalized mappings."""
    if isinstance(result, Path) or isinstance(repository, Path):
        raise AutopilotError("checkpoint arguments cannot be Path instances")
    if isinstance(repository, dict) and isinstance(repository.get("assignment_scope"), AssignmentScope):
        normalized_repo = validate_accepted_repository(repository)
    else:
        normalized_repo = _validate_persisted_repository(repository)
    normalized_result = validate_rescope_receipt(result, str(normalized_repo["head"]))
    return normalized_result, normalized_repo


def serialize_rescope_receipt(
    result: object,
    repository: object,
    attempt: int,
    terminal: bool | None = None,
) -> bytes:
    """Construct and serialize a canonical rescope receipt envelope into deterministic UTF-8 bytes."""
    if isinstance(result, Path) or isinstance(repository, Path):
        raise AutopilotError("rescope receipt arguments cannot be Path instances")
    if type(attempt) is not int or isinstance(attempt, bool):
        raise AutopilotError("attempt must be an integer")
    if attempt not in {1, 2, 3}:
        raise AutopilotError("attempt must be an integer in 1..3")
    expected_terminal = (attempt == 3)
    if terminal is not None:
        if type(terminal) is not bool:
            raise AutopilotError("terminal must be a boolean")
        if terminal is not expected_terminal:
            raise AutopilotError("terminal must be True iff attempt is 3")
    terminal = expected_terminal

    normalized_result, normalized_repo = validate_rescope_checkpoint(result, repository)

    envelope = {
        "schema_version": RESCOPE_RECEIPT_SCHEMA_VERSION,
        "attempt": attempt,
        "terminal": terminal,
        "result": normalized_result,
        "repository": normalized_repo,
    }
    encoded = serialize_json(envelope)
    if len(encoded) > MAX_RESCOPE_RECEIPT_BYTES:
        raise AutopilotError(
            f"serialized rescope receipt exceeds {MAX_RESCOPE_RECEIPT_BYTES} bytes ({len(encoded)} bytes)"
        )
    return encoded


def parse_rescope_receipt(
    raw_bytes: object,
    expected_repository: object,
) -> dict[str, object]:
    """Parse and validate canonical rescope receipt bytes against expected repository facts."""
    if isinstance(raw_bytes, Path) or isinstance(expected_repository, Path):
        raise AutopilotError("parse_rescope_receipt arguments cannot be Path instances")
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise AutopilotError("rescope receipt raw payload must be bytes")
    if len(raw_bytes) > MAX_RESCOPE_RECEIPT_BYTES:
        raise AutopilotError(
            f"rescope receipt exceeds maximum size of {MAX_RESCOPE_RECEIPT_BYTES} bytes ({len(raw_bytes)} bytes)"
        )
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AutopilotError("rescope receipt payload is not valid UTF-8") from exc

    try:
        parsed = unique_json(raw_text)
    except (json.JSONDecodeError, ValueError, ProposalError) as exc:
        raise AutopilotError("rescope receipt payload is malformed or duplicate-keyed JSON") from exc

    if not isinstance(parsed, dict) or set(parsed) != RESCOPE_RECEIPT_ENVELOPE_KEYS:
        raise AutopilotError("rescope receipt envelope keys do not match the exact contract")

    schema_version = parsed.get("schema_version")
    if type(schema_version) is not int or schema_version != RESCOPE_RECEIPT_SCHEMA_VERSION:
        raise AutopilotError("rescope receipt schema_version must be 1")

    attempt = parsed.get("attempt")
    if type(attempt) is not int or isinstance(attempt, bool):
        raise AutopilotError("rescope receipt attempt must be an integer")
    if attempt not in {1, 2, 3}:
        raise AutopilotError("rescope receipt attempt must be in range 1..3")

    terminal = parsed.get("terminal")
    if type(terminal) is not bool:
        raise AutopilotError("rescope receipt terminal must be a boolean")
    if terminal is not (attempt == 3):
        raise AutopilotError("rescope receipt terminal must be True iff attempt is 3")

    normalized_repo = _validate_persisted_repository(parsed.get("repository"))

    if isinstance(expected_repository, dict) and isinstance(expected_repository.get("assignment_scope"), AssignmentScope):
        normalized_expected_repo = validate_accepted_repository(expected_repository)
    else:
        normalized_expected_repo = _validate_persisted_repository(expected_repository)

    if normalized_repo != normalized_expected_repo:
        raise AutopilotError("receipt repository facts do not match expected repository facts")

    repo_head = str(normalized_repo["head"])
    normalized_result = validate_rescope_receipt(parsed.get("result"), repo_head)

    envelope = {
        "schema_version": RESCOPE_RECEIPT_SCHEMA_VERSION,
        "attempt": attempt,
        "terminal": terminal,
        "result": normalized_result,
        "repository": normalized_repo,
    }
    canonical_bytes = serialize_json(envelope)
    if canonical_bytes != raw_bytes:
        raise AutopilotError("receipt bytes are not canonical")

    return envelope


def serialize_next_rescope_receipt(
    previous: object,
    result: object,
    repository: object,
) -> bytes:
    """Validate previous rescope receipt and serialize next attempt bytes."""
    if isinstance(previous, Path) or isinstance(result, Path) or isinstance(repository, Path):
        raise AutopilotError("rescope receipt arguments cannot be Path instances")
    if not isinstance(previous, dict):
        raise AutopilotError("previous rescope receipt must be a mapping")
    try:
        encoded_previous = serialize_json(previous)
    except Exception as exc:
        raise AutopilotError(f"cannot serialize previous rescope receipt: {exc}") from exc
    parsed_previous = parse_rescope_receipt(encoded_previous, repository)
    attempt = parsed_previous.get("attempt")
    if type(attempt) is not int or isinstance(attempt, bool):
        raise AutopilotError("previous attempt must be an integer")
    if attempt not in {1, 2}:
        raise AutopilotError("previous attempt must be 1 or 2 to advance")
    if parsed_previous.get("terminal") is not False:
        raise AutopilotError("previous receipt must not be terminal")
    return serialize_rescope_receipt(result, repository, attempt=attempt + 1)


def build_rescope_recovery_context(
    receipt: object, expected_repository: object
) -> dict[str, object]:
    """Translate a validated canonical rescope receipt into run_loop recovery context."""
    if isinstance(receipt, Path) or isinstance(expected_repository, Path):
        raise AutopilotError("rescope receipt arguments cannot be Path instances")
    if not isinstance(receipt, dict):
        raise AutopilotError("rescope receipt must be a mapping")
    try:
        encoded = serialize_json(receipt)
    except Exception as exc:
        raise AutopilotError(f"cannot serialize rescope receipt: {exc}") from exc
    envelope = parse_rescope_receipt(encoded, expected_repository)
    attempt = envelope.get("attempt")
    if type(attempt) is not int or isinstance(attempt, bool) or attempt not in {1, 2}:
        raise AutopilotError("recovery context requires attempt 1 or 2")
    if envelope.get("terminal") is not False:
        raise AutopilotError("recovery context requires non-terminal receipt")
    result = envelope["result"]
    next_action = result["next_action"]
    blockers = list(result["blockers"])
    context: dict[str, object] = {
        "attempt": attempt,
        "attempt_count": attempt,
        "consecutive_rescopes": attempt,
        "prior_unit_rejected": True,
        "rejected": True,
        "next_action": next_action,
        "blockers": blockers,
        "defer_prior_unit": attempt >= 2,
    }
    if attempt >= 2:
        context["deferred_unit"] = {"next_action": next_action, "blockers": list(blockers)}
    return context


def rescope_epoch_basename(repository: object) -> str:
    """Derive deterministic epoch directory basename from canonical repository facts."""
    if isinstance(repository, Path) or not isinstance(repository, dict):
        raise AutopilotError("repository facts must be a mapping, not Path")
    if isinstance(repository.get("assignment_scope"), AssignmentScope):
        normalized = validate_accepted_repository(repository)
    else:
        normalized = _validate_persisted_repository(repository)
    payload = serialize_json(normalized)
    if len(payload) > MAX_RESCOPE_RECEIPT_BYTES:
        raise AutopilotError(f"repository facts exceed {MAX_RESCOPE_RECEIPT_BYTES} bytes")
    digest = hashlib.sha256(b"two-site-rescope-epoch-v1\0" + payload).hexdigest()
    return f"rescope-{digest}"


class _DescriptorTracker:
    def __init__(self) -> None:
        self.fds: list[int] = []

    def add(self, fd: int) -> None:
        if fd not in self.fds:
            self.fds.append(fd)

    def remove(self, fd: int) -> None:
        if fd in self.fds:
            self.fds.remove(fd)

    def close_all(self, primary_exc: Exception | None = None) -> None:
        active_exc_type, active_exc, _ = sys.exc_info()
        is_interrupt = (
            (active_exc_type is not None and issubclass(active_exc_type, (KeyboardInterrupt, SystemExit)))
            or isinstance(primary_exc, (KeyboardInterrupt, SystemExit))
        )
        close_errors: list[OSError] = []
        for fd in list(self.fds):
            try:
                os.close(fd)
            except OSError as ce:
                close_errors.append(ce)
            finally:
                self.remove(fd)
        if close_errors:
            if is_interrupt:
                return
            first_err = close_errors[0]
            err = AutopilotError(f"error closing descriptor: {first_err}")
            if primary_exc is not None:
                err.__cause__ = primary_exc
            elif active_exc is not None and isinstance(active_exc, Exception):
                err.__cause__ = active_exc
            else:
                err.__cause__ = first_err
            raise err


def _recheck_final_root(state_root: Path, pinned_dir_fd: int) -> None:
    try:
        resolved = state_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AutopilotError(f"final state_root cannot be resolved: {exc}") from exc
    if resolved != state_root:
        raise AutopilotError("state_root canonical path changed")
    try:
        st = state_root.stat(follow_symlinks=False)
    except OSError as exc:
        raise AutopilotError(f"cannot stat final state_root: {exc}") from exc
    try:
        pinned_st = os.fstat(pinned_dir_fd)
    except OSError as exc:
        raise AutopilotError(f"cannot fstat pinned directory: {exc}") from exc
    if (st.st_dev, st.st_ino) != (pinned_st.st_dev, pinned_st.st_ino):
        raise AutopilotError("state_root directory inode changed")
    if (pinned_st.st_mode & 0o777) != 0o700 or pinned_st.st_uid != os.geteuid():
        raise AutopilotError("pinned directory permissions invalid")


def _open_private_rescope_state_dir(state_root: Path) -> int:
    """Open and pin state_root securely, returning an owned directory file descriptor."""
    if not isinstance(state_root, Path):
        raise AutopilotError("state_root must be a Path")
    if not state_root.is_absolute():
        raise AutopilotError("state_root must be an absolute Path")
    if ".." in state_root.parts or "." in state_root.parts or state_root != Path(os.path.abspath(str(state_root))):
        raise AutopilotError("state_root must be normalized without relative components")
    try:
        resolved_root = state_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AutopilotError(f"state_root cannot be resolved: {exc}") from exc
    if resolved_root != state_root:
        raise AutopilotError("state_root cannot contain symlinks or path aliases")

    if (
        getattr(os, "O_NOFOLLOW", None) is None
        or getattr(os, "O_DIRECTORY", None) is None
        or getattr(os, "O_CLOEXEC", None) is None
    ):
        raise AutopilotError("platform missing required O_NOFOLLOW, O_DIRECTORY, or O_CLOEXEC flags")
    supports_dir_fd = getattr(os, "supports_dir_fd", set())
    has_open_dir_fd = os.open in supports_dir_fd or any(getattr(f, "__name__", None) == "open" for f in supports_dir_fd)
    has_stat_dir_fd = os.stat in supports_dir_fd or any(getattr(f, "__name__", None) == "stat" for f in supports_dir_fd)
    if not has_open_dir_fd or not has_stat_dir_fd:
        raise AutopilotError("platform missing dir_fd support for os.open or os.stat")

    tracker = _DescriptorTracker()
    primary_exc: Exception | None = None
    try:
        dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

        try:
            current_fd = os.open("/", dir_flags)
        except TypeError as exc:
            raise AutopilotError(f"os.open failed with TypeError: {exc}") from exc
        except OSError as exc:
            raise AutopilotError(f"cannot open root directory: {exc}") from exc

        tracker.add(current_fd)

        for comp in state_root.parts[1:]:
            try:
                next_fd = os.open(comp, dir_flags, dir_fd=current_fd)
            except TypeError as exc:
                raise AutopilotError(f"dir_fd open failed with TypeError: {exc}") from exc
            except OSError as exc:
                raise AutopilotError(f"cannot open directory component {comp!r}: {exc}") from exc

            tracker.add(next_fd)
            try:
                os.close(current_fd)
            except OSError as exc:
                raise AutopilotError(f"failed to close predecessor directory descriptor: {exc}") from exc
            finally:
                tracker.remove(current_fd)
            current_fd = next_fd

        pinned_dir_fd = current_fd
        try:
            dir_stat = os.fstat(pinned_dir_fd)
        except OSError as exc:
            raise AutopilotError(f"cannot fstat state_root directory: {exc}") from exc

        if not stat.S_ISDIR(dir_stat.st_mode):
            raise AutopilotError("state_root is not a directory")
        if dir_stat.st_uid != os.geteuid():
            raise AutopilotError("state_root directory owner must be process euid")
        if (dir_stat.st_mode & 0o777) != 0o700:
            raise AutopilotError("state_root directory mode must be 0700")

        try:
            expected_stat = state_root.stat(follow_symlinks=False)
        except OSError as exc:
            raise AutopilotError(f"cannot stat state_root path: {exc}") from exc

        if (dir_stat.st_dev, dir_stat.st_ino) != (expected_stat.st_dev, expected_stat.st_ino):
            raise AutopilotError("pinned directory does not match state_root inode")

        _recheck_final_root(state_root, pinned_dir_fd)

        tracker.remove(pinned_dir_fd)
        return pinned_dir_fd
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        primary_exc = exc
        raise
    finally:
        tracker.close_all(primary_exc)


def ensure_rescope_epoch_dir(state_root: Path, repository: object) -> Path:
    """Ensure private epoch directory exists, validating state and directory invariants."""
    if not isinstance(state_root, Path):
        raise AutopilotError("state_root must be a Path")
    basename = rescope_epoch_basename(repository)
    child_path = state_root / basename
    parent_fd = _open_private_rescope_state_dir(state_root)
    primary_exc: BaseException | None = None
    try:
        try:
            os.mkdir(basename, mode=0o700, dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise AutopilotError(f"ambiguous: cannot mkdir epoch dir: {exc}") from exc
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise AutopilotError(f"ambiguous: unexpected mkdir error: {exc}") from exc

        try:
            child_fd = _open_private_rescope_state_dir(child_path)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise AutopilotError(f"ambiguous: cannot open epoch child dir: {exc}") from exc

        try:
            os.close(child_fd)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise AutopilotError(f"ambiguous: cannot close epoch child fd: {exc}") from exc

        try:
            os.fsync(parent_fd)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise AutopilotError(f"ambiguous: cannot fsync parent dir: {exc}") from exc

        return child_path
    except BaseException as exc:
        primary_exc = exc
        raise
    finally:
        is_interrupt = isinstance(primary_exc, (KeyboardInterrupt, SystemExit))
        try:
            os.close(parent_fd)
        except Exception as close_err:
            if not is_interrupt:
                close_exc = AutopilotError(f"ambiguous: error closing parent dir: {close_err}")
                if primary_exc is not None and isinstance(primary_exc, Exception):
                    close_exc.__cause__ = primary_exc
                else:
                    close_exc.__cause__ = close_err
                raise close_exc


_ensure_rescope_epoch_dir = ensure_rescope_epoch_dir


def rescope_intent_exists(epoch_dir: Path, attempt: int) -> bool:
    """Check existence of the bounded rescope intent leaf for attempt in 1..3."""
    if not isinstance(epoch_dir, Path):
        raise AutopilotError("epoch_dir must be a Path")
    if type(attempt) is not int or isinstance(attempt, bool):
        raise AutopilotError("attempt must be an integer")
    if attempt not in _RESCOPE_INTENT_FILENAMES:
        raise AutopilotError("attempt must be an integer in 1..3")

    leaf_name = _RESCOPE_INTENT_FILENAMES[attempt]
    pinned_fd = _open_private_rescope_state_dir(epoch_dir)
    primary_exc: BaseException | None = None
    try:
        try:
            os.stat(leaf_name, dir_fd=pinned_fd, follow_symlinks=False)
            exists = True
        except TypeError as exc:
            raise AutopilotError(f"os.stat failed with TypeError: {exc}") from exc
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                exists = False
            else:
                raise AutopilotError(f"cannot stat rescope intent leaf: {exc}") from exc

        _recheck_final_root(epoch_dir, pinned_fd)
        return exists
    except BaseException as exc:
        primary_exc = exc
        raise
    finally:
        active_exc_type, active_exc, _ = sys.exc_info()
        is_interrupt = (
            (active_exc_type is not None and issubclass(active_exc_type, (KeyboardInterrupt, SystemExit)))
            or isinstance(primary_exc, (KeyboardInterrupt, SystemExit))
        )
        try:
            os.close(pinned_fd)
        except (KeyboardInterrupt, SystemExit):
            raise
        except OSError as close_err:
            if not is_interrupt:
                close_exc = AutopilotError(f"error closing pinned directory: {close_err}")
                if primary_exc is not None and isinstance(primary_exc, Exception):
                    close_exc.__cause__ = primary_exc
                elif active_exc is not None and isinstance(active_exc, Exception):
                    close_exc.__cause__ = active_exc
                else:
                    close_exc.__cause__ = close_err
                raise close_exc


def publish_rescope_intent(epoch_dir: Path, attempt: int) -> None:
    """Publish a durable rescope intent marker for attempt in 1..3."""
    if not isinstance(epoch_dir, Path):
        raise AutopilotError("epoch_dir must be a Path")
    if type(attempt) is not int or isinstance(attempt, bool):
        raise AutopilotError("attempt must be an integer")
    if attempt not in _RESCOPE_INTENT_FILENAMES:
        raise AutopilotError("attempt must be an integer in 1..3")

    leaf_name = _RESCOPE_INTENT_FILENAMES[attempt]
    pinned_fd = _open_private_rescope_state_dir(epoch_dir)
    file_fd: int | None = None
    primary_exc: BaseException | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            file_fd = os.open(leaf_name, flags, 0o600, dir_fd=pinned_fd)
        except TypeError as exc:
            raise AutopilotError(f"os.open failed with TypeError: {exc}") from exc
        except OSError as exc:
            raise AutopilotError(f"cannot open rescope intent leaf: {exc}") from exc

        payload = f"{leaf_name}\n".encode("ascii")
        total_written = 0
        view = memoryview(payload)
        for _ in range(len(payload) + 5):
            try:
                written = os.write(file_fd, view[total_written:])
            except OSError as exc:
                raise AutopilotError(f"cannot write rescope intent leaf: {exc}") from exc
            if written <= 0:
                raise AutopilotError(f"invalid write count on rescope intent leaf: {written}")
            total_written += written
            if total_written >= len(payload):
                break
        else:
            raise AutopilotError("partial write loop exceeded maximum iterations")

        try:
            st = os.fstat(file_fd)
        except OSError as exc:
            raise AutopilotError(f"cannot fstat rescope intent leaf: {exc}") from exc
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.geteuid()
            or (st.st_mode & 0o777) != 0o600
            or st.st_nlink != 1
            or st.st_size != len(payload)
        ):
            raise AutopilotError("rescope intent leaf metadata invalid")

        try:
            os.fsync(file_fd)
        except OSError as exc:
            raise AutopilotError(f"cannot fsync rescope intent leaf: {exc}") from exc

        try:
            os.fsync(pinned_fd)
        except OSError as exc:
            raise AutopilotError(f"cannot fsync pinned directory: {exc}") from exc

        _recheck_final_root(epoch_dir, pinned_fd)

        try:
            leaf_st = os.stat(leaf_name, dir_fd=pinned_fd, follow_symlinks=False)
        except TypeError as exc:
            raise AutopilotError(f"os.stat failed with TypeError: {exc}") from exc
        except OSError as exc:
            raise AutopilotError(f"cannot stat rescope intent leaf: {exc}") from exc

        if (leaf_st.st_dev, leaf_st.st_ino) != (st.st_dev, st.st_ino):
            raise AutopilotError("rescope intent leaf inode mismatch")
    except BaseException as exc:
        primary_exc = exc
        raise
    finally:
        active_exc_type, active_exc, _ = sys.exc_info()
        close_errors: list[OSError] = []
        interrupt_exc: BaseException | None = None
        if file_fd is not None:
            try:
                os.close(file_fd)
            except (KeyboardInterrupt, SystemExit) as exc:
                interrupt_exc = exc
            except OSError as exc:
                close_errors.append(exc)
        try:
            os.close(pinned_fd)
        except (KeyboardInterrupt, SystemExit) as exc:
            if interrupt_exc is None:
                interrupt_exc = exc
        except OSError as exc:
            close_errors.append(exc)
        is_interrupt = (
            (active_exc_type is not None and issubclass(active_exc_type, (KeyboardInterrupt, SystemExit)))
            or isinstance(primary_exc, (KeyboardInterrupt, SystemExit))
            or interrupt_exc is not None
        )
        if is_interrupt:
            if interrupt_exc is not None:
                raise interrupt_exc
        elif close_errors:
            first_close_err = close_errors[0]
            close_exc = AutopilotError(f"error closing descriptor: {first_close_err}")
            if primary_exc is not None and isinstance(primary_exc, Exception):
                close_exc.__cause__ = primary_exc
            elif active_exc is not None and isinstance(active_exc, Exception):
                close_exc.__cause__ = active_exc
            else:
                close_exc.__cause__ = first_close_err
            raise close_exc


def load_rescope_receipt(
    state_root: Path,
    expected_repository: object,
    receipt_filename: str = _RESCOPE_RECEIPT_FILENAME,
) -> dict[str, object] | None:
    """Safely load and validate the canonical rescope receipt under state_root.

    Returns the parsed envelope dict, or None if the leaf receipt does not exist.
    Fails closed with typed AutopilotError on any directory traversal defect,
    permission/ownership defect, symlink, link count, size, drift, or codec error.
    """
    if type(receipt_filename) is not str or receipt_filename not in _RESCOPE_RECEIPT_FILENAMES:
        raise AutopilotError(f"invalid rescope receipt filename: {receipt_filename!r}")
    if not isinstance(state_root, Path):
        raise AutopilotError("state_root must be a Path")
    if not state_root.is_absolute():
        raise AutopilotError("state_root must be an absolute Path")
    if ".." in state_root.parts or "." in state_root.parts or state_root != Path(os.path.abspath(str(state_root))):
        raise AutopilotError("state_root must be normalized without relative components")
    try:
        resolved_root = state_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AutopilotError(f"state_root cannot be resolved: {exc}") from exc
    if resolved_root != state_root:
        raise AutopilotError("state_root cannot contain symlinks or path aliases")

    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise AutopilotError("platform missing required O_NOFOLLOW or O_DIRECTORY flags")
    supports_dir_fd = getattr(os, "supports_dir_fd", set())
    has_open_dir_fd = os.open in supports_dir_fd or any(getattr(f, "__name__", None) == "open" for f in supports_dir_fd)
    has_stat_dir_fd = os.stat in supports_dir_fd or any(getattr(f, "__name__", None) == "stat" for f in supports_dir_fd)
    if not has_open_dir_fd or not has_stat_dir_fd:
        raise AutopilotError("platform missing dir_fd support for os.open or os.stat")

    tracker = _DescriptorTracker()
    primary_exc: Exception | None = None
    try:
        dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            dir_flags |= os.O_CLOEXEC

        try:
            current_fd = os.open("/", dir_flags)
        except TypeError as exc:
            raise AutopilotError(f"os.open failed with TypeError: {exc}") from exc
        except OSError as exc:
            raise AutopilotError(f"cannot open root directory: {exc}") from exc

        tracker.add(current_fd)

        for comp in state_root.parts[1:]:
            try:
                next_fd = os.open(comp, dir_flags, dir_fd=current_fd)
            except TypeError as exc:
                raise AutopilotError(f"dir_fd open failed with TypeError: {exc}") from exc
            except OSError as exc:
                raise AutopilotError(f"cannot open directory component {comp!r}: {exc}") from exc

            tracker.add(next_fd)
            try:
                os.close(current_fd)
            except OSError as exc:
                raise AutopilotError(f"failed to close predecessor directory descriptor: {exc}") from exc
            finally:
                tracker.remove(current_fd)
            current_fd = next_fd

        pinned_dir_fd = current_fd
        try:
            dir_stat = os.fstat(pinned_dir_fd)
        except OSError as exc:
            raise AutopilotError(f"cannot fstat state_root directory: {exc}") from exc

        if not stat.S_ISDIR(dir_stat.st_mode):
            raise AutopilotError("state_root is not a directory")
        if dir_stat.st_uid != os.geteuid():
            raise AutopilotError("state_root directory owner must be process euid")
        if (dir_stat.st_mode & 0o777) != 0o700:
            raise AutopilotError("state_root directory mode must be 0700")

        try:
            expected_stat = state_root.stat(follow_symlinks=False)
        except OSError as exc:
            raise AutopilotError(f"cannot stat state_root path: {exc}") from exc

        if (dir_stat.st_dev, dir_stat.st_ino) != (expected_stat.st_dev, expected_stat.st_ino):
            raise AutopilotError("pinned directory does not match state_root inode")

        leaf_name = receipt_filename
        try:
            pre_stat = os.stat(leaf_name, dir_fd=pinned_dir_fd, follow_symlinks=False)
            if stat.S_ISLNK(pre_stat.st_mode):
                raise AutopilotError("receipt leaf is a symlink")
        except FileNotFoundError:
            pass
        except TypeError as exc:
            raise AutopilotError(f"os.stat failed with TypeError: {exc}") from exc
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise AutopilotError(f"pre-stat error on leaf: {exc}") from exc

        leaf_flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            leaf_flags |= os.O_CLOEXEC
        if hasattr(os, "O_NONBLOCK"):
            leaf_flags |= os.O_NONBLOCK

        try:
            leaf_fd = os.open(leaf_name, leaf_flags, dir_fd=pinned_dir_fd)
            tracker.add(leaf_fd)
        except TypeError as exc:
            raise AutopilotError(f"os.open failed with TypeError: {exc}") from exc
        except (FileNotFoundError, OSError) as exc:
            is_enoent = isinstance(exc, FileNotFoundError) or (isinstance(exc, OSError) and exc.errno == errno.ENOENT)
            if is_enoent:
                _recheck_final_root(state_root, pinned_dir_fd)
                return None
            raise AutopilotError(f"cannot open rescope receipt leaf: {exc}") from exc

        try:
            leaf_stat_before = os.fstat(leaf_fd)
        except OSError as exc:
            raise AutopilotError(f"cannot fstat receipt leaf: {exc}") from exc

        if not stat.S_ISREG(leaf_stat_before.st_mode):
            raise AutopilotError("rescope receipt must be a regular file")
        if leaf_stat_before.st_nlink != 1:
            raise AutopilotError("rescope receipt must have link count 1")
        if leaf_stat_before.st_uid != os.geteuid():
            raise AutopilotError("rescope receipt owner must match process euid")
        if (leaf_stat_before.st_mode & 0o777) != 0o600:
            raise AutopilotError("rescope receipt mode must be 0600")
        if leaf_stat_before.st_size > MAX_RESCOPE_RECEIPT_BYTES:
            raise AutopilotError(f"rescope receipt size exceeds {MAX_RESCOPE_RECEIPT_BYTES} bytes")

        max_read = MAX_RESCOPE_RECEIPT_BYTES + 1
        chunks = []
        total_read = 0
        while total_read < max_read:
            to_read = min(4096, max_read - total_read)
            try:
                chunk = os.read(leaf_fd, to_read)
            except OSError as exc:
                raise AutopilotError(f"error reading rescope receipt: {exc}") from exc
            if not chunk:
                break
            chunks.append(chunk)
            total_read += len(chunk)

        raw_bytes = b"".join(chunks)
        if len(raw_bytes) > MAX_RESCOPE_RECEIPT_BYTES:
            raise AutopilotError(f"rescope receipt exceeds maximum size of {MAX_RESCOPE_RECEIPT_BYTES} bytes")

        try:
            leaf_stat_after = os.fstat(leaf_fd)
        except OSError as exc:
            raise AutopilotError(f"cannot fstat receipt leaf after read: {exc}") from exc

        if file_fingerprint(leaf_stat_before) != file_fingerprint(leaf_stat_after):
            raise AutopilotError("rescope receipt file fingerprint changed during read")

        try:
            leaf_stat_dir = os.stat(leaf_name, dir_fd=pinned_dir_fd, follow_symlinks=False)
        except TypeError as exc:
            raise AutopilotError(f"os.stat failed with TypeError: {exc}") from exc
        except OSError as exc:
            raise AutopilotError(f"cannot stat receipt leaf in pinned directory: {exc}") from exc

        if file_fingerprint(leaf_stat_after) != file_fingerprint(leaf_stat_dir):
            raise AutopilotError("receipt leaf in directory does not match open descriptor")

        _recheck_final_root(state_root, pinned_dir_fd)

        envelope = parse_rescope_receipt(raw_bytes, expected_repository)
        if envelope.get("attempt") != _RESCOPE_RECEIPT_FILENAMES[receipt_filename]:
            raise AutopilotError(
                f"receipt attempt {envelope.get('attempt')!r} does not match expected attempt for {receipt_filename}"
            )
        return envelope
    except Exception as exc:
        primary_exc = exc
        raise
    finally:
        tracker.close_all(primary_exc)


_load_rescope_receipt = load_rescope_receipt


def load_latest_rescope_receipt(
    state_root: Path,
    expected_repository: object,
) -> dict[str, object] | None:
    """Safely load the latest rescope receipt, requiring a contiguous prefix in 1..3."""
    receipts = [
        load_rescope_receipt(state_root, expected_repository, filename)
        for filename in _RESCOPE_RECEIPT_FILENAMES
    ]
    r1, r2, r3 = receipts
    if (r2 is not None and r1 is None) or (r3 is not None and r2 is None):
        raise AutopilotError("non-contiguous rescope receipt sequence")
    if r3 is not None:
        return r3
    if r2 is not None:
        return r2
    return r1


_load_latest_rescope_receipt = load_latest_rescope_receipt


def _create_rescope_temp_file(pinned_dir_fd: int, payload: bytes) -> str:
    """Safely create, populate, fsync, and close a private temp file in pinned_dir_fd.

    Returns the unique hidden basename on success.
    Fails closed with typed AutopilotError on any validation, I/O, or attribute defect,
    cleaning up any created temp file.
    """
    if type(pinned_dir_fd) is not int or isinstance(pinned_dir_fd, bool):
        raise AutopilotError("pinned_dir_fd must be an integer")
    if pinned_dir_fd < 0:
        raise AutopilotError("pinned_dir_fd must be non-negative")
    if type(payload) is not bytes:
        raise AutopilotError("payload must be bytes")
    if not (1 <= len(payload) <= MAX_RESCOPE_RECEIPT_BYTES):
        raise AutopilotError(
            f"payload length must be in range 1..{MAX_RESCOPE_RECEIPT_BYTES} bytes"
        )
    if (
        getattr(os, "O_NOFOLLOW", None) is None
        or getattr(os, "O_CLOEXEC", None) is None
        or getattr(os, "O_DIRECTORY", None) is None
    ):
        raise AutopilotError("platform missing required O_NOFOLLOW, O_CLOEXEC, or O_DIRECTORY flags")
    supports_dir_fd = getattr(os, "supports_dir_fd", set())
    has_open_dir_fd = os.open in supports_dir_fd or any(getattr(f, "__name__", None) == "open" for f in supports_dir_fd)
    has_unlink_dir_fd = os.unlink in supports_dir_fd or any(getattr(f, "__name__", None) == "unlink" for f in supports_dir_fd)
    if not has_open_dir_fd or not has_unlink_dir_fd:
        raise AutopilotError("platform missing dir_fd support for os.open or os.unlink")

    try:
        dir_stat = os.fstat(pinned_dir_fd)
    except OSError as exc:
        raise AutopilotError(f"cannot fstat pinned directory fd: {exc}") from exc

    if not stat.S_ISDIR(dir_stat.st_mode):
        raise AutopilotError("pinned directory fd is not a directory")
    if dir_stat.st_uid != os.geteuid():
        raise AutopilotError("pinned directory owner must be process euid")
    if (dir_stat.st_mode & 0o777) != 0o700:
        raise AutopilotError("pinned directory mode must be 0700")

    temp_name = f".rescope_receipt_tmp_{os.getpid()}_{os.urandom(8).hex()}"
    temp_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC

    try:
        temp_fd = os.open(temp_name, temp_flags, 0o600, dir_fd=pinned_dir_fd)
    except TypeError as exc:
        raise AutopilotError(f"os.open failed with TypeError: {exc}") from exc
    except OSError as exc:
        raise AutopilotError(f"cannot open temporary rescope file: {exc}") from exc

    try:
        try:
            temp_stat = os.fstat(temp_fd)
        except OSError as exc:
            raise AutopilotError(f"cannot fstat temporary rescope file: {exc}") from exc

        if not stat.S_ISREG(temp_stat.st_mode):
            raise AutopilotError("temporary rescope file is not a regular file")
        if temp_stat.st_uid != os.geteuid():
            raise AutopilotError("temporary rescope file owner must be process euid")
        if (temp_stat.st_mode & 0o777) != 0o600:
            raise AutopilotError("temporary rescope file mode must be exactly 0600")
        if temp_stat.st_nlink != 1:
            raise AutopilotError("temporary rescope file link count must be 1")

        total_written = 0
        view = memoryview(payload)
        while total_written < len(payload):
            try:
                written = os.write(temp_fd, view[total_written:])
            except OSError as exc:
                raise AutopilotError(f"error writing temporary rescope file: {exc}") from exc
            if written <= 0:
                raise AutopilotError(f"zero or negative write count on temporary rescope file: {written}")
            total_written += written

        try:
            os.fsync(temp_fd)
        except OSError as exc:
            raise AutopilotError(f"cannot fsync temporary rescope file: {exc}") from exc

        fd_to_close = temp_fd
        temp_fd = None
        try:
            os.close(fd_to_close)
        except OSError as exc:
            raise AutopilotError(f"cannot close temporary rescope file: {exc}") from exc

        return temp_name

    except (KeyboardInterrupt, SystemExit):
        if temp_fd is not None:
            try:
                os.close(temp_fd)
            except OSError:
                pass
            temp_fd = None
        try:
            os.unlink(temp_name, dir_fd=pinned_dir_fd)
        except OSError:
            pass
        raise
    except Exception as exc:
        cleanup_errors: list[OSError] = []
        if temp_fd is not None:
            try:
                os.close(temp_fd)
            except OSError as close_err:
                cleanup_errors.append(close_err)
            temp_fd = None
        try:
            os.unlink(temp_name, dir_fd=pinned_dir_fd)
        except OSError as unlink_err:
            cleanup_errors.append(unlink_err)

        if cleanup_errors:
            err = AutopilotError(f"error cleaning up temporary rescope file: {cleanup_errors[0]}")
            err.__cause__ = exc
            raise err
        if isinstance(exc, AutopilotError):
            raise
        raise AutopilotError(f"temporary rescope file operation failed: {exc}") from exc


def _prove_rescope_link_collision(
    pinned_dir_fd: int,
    temp_name: str,
    link_error: OSError,
    target_filename: str = _RESCOPE_RECEIPT_FILENAME,
) -> None:
    """Classify a rescope receipt link error, returning None only for a proven collision."""
    if not isinstance(link_error, OSError):
        raise AutopilotError("ambiguous: link_error must be an OSError")
    if type(pinned_dir_fd) is not int or isinstance(pinned_dir_fd, bool) or pinned_dir_fd < 0:
        raise AutopilotError("ambiguous: pinned_dir_fd must be a non-negative integer") from link_error
    if (
        type(temp_name) is not str
        or not temp_name.startswith(".rescope_receipt_tmp_")
        or "/" in temp_name
        or "\\" in temp_name
        or "\x00" in temp_name
        or ".." in temp_name
        or os.path.basename(temp_name) != temp_name
    ):
        raise AutopilotError(
            "ambiguous: temp_name must be a safe basename starting with .rescope_receipt_tmp_"
        ) from link_error
    if type(target_filename) is not str or target_filename not in _RESCOPE_RECEIPT_FILENAMES:
        raise AutopilotError(
            f"ambiguous: invalid target_filename: {target_filename!r}"
        ) from link_error

    supports_dir_fd = getattr(os, "supports_dir_fd", set())
    has_stat_dir_fd = os.stat in supports_dir_fd or any(
        getattr(f, "__name__", None) == "stat" for f in supports_dir_fd
    )
    if not has_stat_dir_fd:
        raise AutopilotError("ambiguous: platform missing dir_fd support for os.stat") from link_error

    if getattr(link_error, "errno", None) != errno.EEXIST:
        raise AutopilotError(f"ambiguous: link_error errno is not EEXIST: {link_error}") from link_error

    try:
        temp_st = os.stat(temp_name, dir_fd=pinned_dir_fd, follow_symlinks=False)
    except TypeError as exc:
        raise AutopilotError(f"ambiguous: os.stat temp failed with TypeError: {exc}") from exc
    except OSError as exc:
        raise AutopilotError(f"ambiguous: cannot stat temporary rescope file: {exc}") from exc

    if not stat.S_ISREG(temp_st.st_mode):
        raise AutopilotError("ambiguous: temporary rescope file is not a regular file") from link_error
    if temp_st.st_uid != os.geteuid():
        raise AutopilotError("ambiguous: temporary rescope file owner does not match current euid") from link_error
    if (temp_st.st_mode & 0o777) != 0o600:
        raise AutopilotError("ambiguous: temporary rescope file mode is not 0600") from link_error
    if temp_st.st_nlink != 1:
        raise AutopilotError(
            f"ambiguous: temporary rescope file link count is {temp_st.st_nlink}, expected 1"
        ) from link_error

    try:
        target_st = os.stat(target_filename, dir_fd=pinned_dir_fd, follow_symlinks=False)
    except TypeError as exc:
        raise AutopilotError(f"ambiguous: os.stat target failed with TypeError: {exc}") from exc
    except OSError as exc:
        raise AutopilotError(f"ambiguous: cannot stat target rescope receipt: {exc}") from exc

    if (target_st.st_dev, target_st.st_ino) == (temp_st.st_dev, temp_st.st_ino):
        raise AutopilotError("ambiguous: target receipt shares inode with temporary file") from link_error

    return None


def _publish_rescope_payload_noreplace(
    state_root: Path,
    payload: bytes,
    repository: object,
    target_filename: str,
) -> None:
    """Atomically publish a rescope receipt payload under state_root without overwriting."""
    if not isinstance(state_root, Path):
        raise AutopilotError("state_root must be a Path")
    if type(payload) is not bytes:
        raise AutopilotError("payload must be bytes")
    if type(target_filename) is not str or target_filename not in _RESCOPE_RECEIPT_FILENAMES:
        raise AutopilotError(f"invalid target_filename: {target_filename!r}")

    expected_envelope = parse_rescope_receipt(payload, repository)
    if expected_envelope.get("attempt") != _RESCOPE_RECEIPT_FILENAMES[target_filename]:
        raise AutopilotError(
            f"receipt attempt {expected_envelope.get('attempt')!r} does not match expected attempt for {target_filename}"
        )

    pinned_dir_fd: int | None = None
    temp_name: str | None = None
    link_entered = False
    linked = False
    primary_exc: BaseException | None = None

    try:
        pinned_dir_fd = _open_private_rescope_state_dir(state_root)

        try:
            os.stat(target_filename, dir_fd=pinned_dir_fd, follow_symlinks=False)
            raise AutopilotError("target receipt already exists")
        except FileNotFoundError:
            pass
        except TypeError as exc:
            raise AutopilotError(f"os.stat failed with TypeError: {exc}") from exc
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise AutopilotError(f"cannot stat target receipt: {exc}") from exc

        temp_name = _create_rescope_temp_file(pinned_dir_fd, payload)

        try:
            link_entered = True
            os.link(
                temp_name,
                target_filename,
                src_dir_fd=pinned_dir_fd,
                dst_dir_fd=pinned_dir_fd,
                follow_symlinks=False,
            )
            linked = True
        except OSError as link_err:
            try:
                _prove_rescope_link_collision(pinned_dir_fd, temp_name, link_err, target_filename=target_filename)
            except AutopilotError:
                raise
            except Exception as helper_exc:
                raise AutopilotError(f"ambiguous: link collision helper failed: {helper_exc}") from helper_exc

            try:
                os.unlink(temp_name, dir_fd=pinned_dir_fd)
            except OSError as unlink_err:
                err = AutopilotError(f"error cleaning up temporary rescope file: {unlink_err}")
                err.__cause__ = link_err
                raise err
            raise AutopilotError(f"rescope receipt collision: target already exists ({link_err})") from link_err
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as link_exc:
            raise AutopilotError(f"ambiguous: os.link failed with unexpected exception: {link_exc}") from link_exc

        try:
            try:
                os.unlink(temp_name, dir_fd=pinned_dir_fd)
            except OSError as exc:
                raise AutopilotError(f"ambiguous: failed to unlink temporary receipt: {exc}") from exc

            try:
                os.fsync(pinned_dir_fd)
            except OSError as exc:
                raise AutopilotError(f"ambiguous: failed to fsync pinned directory: {exc}") from exc

            try:
                _recheck_final_root(state_root, pinned_dir_fd)
            except Exception as exc:
                raise AutopilotError(f"ambiguous: final state root recheck failed: {exc}") from exc

            try:
                loaded = load_rescope_receipt(state_root, repository, receipt_filename=target_filename)
            except Exception as exc:
                raise AutopilotError(f"ambiguous: failed to load rescope receipt: {exc}") from exc

            if loaded != expected_envelope:
                raise AutopilotError("ambiguous: loaded receipt does not match expected envelope")
        except (KeyboardInterrupt, SystemExit):
            raise
        except AutopilotError as exc:
            if str(exc).startswith("ambiguous"):
                raise
            raise AutopilotError(f"ambiguous: post-link validation failed: {exc}") from exc
        except Exception as exc:
            raise AutopilotError(f"ambiguous: post-link failure: {exc}") from exc

    except (KeyboardInterrupt, SystemExit) as exc:
        primary_exc = exc
        if temp_name is not None and not link_entered and pinned_dir_fd is not None:
            try:
                os.unlink(temp_name, dir_fd=pinned_dir_fd)
            except OSError:
                pass
        raise
    except Exception as exc:
        primary_exc = exc
        if temp_name is not None and not link_entered and pinned_dir_fd is not None:
            try:
                os.unlink(temp_name, dir_fd=pinned_dir_fd)
            except OSError as unlink_err:
                cleanup_err = AutopilotError(f"error cleaning up temporary rescope file: {unlink_err}")
                cleanup_err.__cause__ = exc
                primary_exc = cleanup_err
        raise primary_exc
    finally:
        active_exc_type, active_exc, _ = sys.exc_info()
        is_interrupt = (
            (active_exc_type is not None and issubclass(active_exc_type, (KeyboardInterrupt, SystemExit)))
            or isinstance(primary_exc, (KeyboardInterrupt, SystemExit))
        )
        if pinned_dir_fd is not None:
            try:
                os.close(pinned_dir_fd)
            except OSError as close_err:
                if not is_interrupt:
                    if link_entered:
                        close_exc = AutopilotError(f"ambiguous: error closing pinned directory: {close_err}")
                    else:
                        close_exc = AutopilotError(f"error closing pinned directory: {close_err}")
                    if primary_exc is not None and isinstance(primary_exc, Exception):
                        close_exc.__cause__ = primary_exc
                    elif active_exc is not None and isinstance(active_exc, Exception):
                        close_exc.__cause__ = active_exc
                    else:
                        close_exc.__cause__ = close_err
                    raise close_exc


def publish_initial_rescope_receipt(
    state_root: Path,
    result: object,
    repository: object,
) -> None:
    """Atomically publish the initial (attempt=1) rescope receipt under state_root.

    Fails closed without overwriting existing receipts or deleting uncertain evidence.
    """
    if not isinstance(state_root, Path):
        raise AutopilotError("state_root must be a Path")

    payload = serialize_rescope_receipt(result, repository, attempt=1)
    _publish_rescope_payload_noreplace(
        state_root,
        payload,
        repository,
        _RESCOPE_RECEIPT_FILENAME,
    )


def publish_next_rescope_receipt(
    state_root: Path,
    result: object,
    repository: object,
) -> None:
    """Atomically publish the sequential next rescope receipt under state_root."""
    if not isinstance(state_root, Path):
        raise AutopilotError("state_root must be a Path")

    latest = load_latest_rescope_receipt(state_root, repository)
    if latest is None:
        raise AutopilotError("no prior rescope receipt found; attempt 1 requires publish_initial_rescope_receipt")

    payload = serialize_next_rescope_receipt(latest, result, repository)
    next_attempt = latest.get("attempt") + 1 if isinstance(latest.get("attempt"), int) else None
    target_filename = next(
        (name for name, att in _RESCOPE_RECEIPT_FILENAMES.items() if att == next_attempt),
        None,
    )
    if target_filename is None:
        raise AutopilotError(f"unsupported next rescope attempt: {next_attempt}")

    _publish_rescope_payload_noreplace(state_root, payload, repository, target_filename)

    try:
        expected = parse_rescope_receipt(payload, repository)
        observed = load_latest_rescope_receipt(state_root, repository)
        if observed != expected:
            raise AutopilotError("ambiguous: latest rescope receipt does not match expected envelope")
    except (KeyboardInterrupt, SystemExit):
        raise
    except AutopilotError as exc:
        if str(exc).startswith("ambiguous"):
            raise
        raise AutopilotError(f"ambiguous: post-publication validation failed: {exc}") from exc
    except Exception as exc:
        raise AutopilotError(f"ambiguous: post-publication validation failed: {exc}") from exc


def validate_rescope_epoch_startup(
    state_root: Path,
    repository: object,
) -> None:
    """Fail-closed startup gate verifying epoch directories and accepted checkpoint."""
    if not isinstance(state_root, Path):
        raise AutopilotError("state_root must be a Path")
    current_epoch = rescope_epoch_basename(repository)
    has_prior_epoch = False
    total_entries = 0
    try:
        with os.scandir(state_root) as scanner:
            for entry in scanner:
                total_entries += 1
                if total_entries > 512:
                    raise AutopilotError("state_root entries exceed maximum limit")
                if entry.name.startswith("rescope-"):
                    if not re.fullmatch(r"rescope-[0-9a-f]{64}", entry.name):
                        raise AutopilotError(f"malformed rescope epoch name: {entry.name}")
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise AutopilotError(f"cannot stat epoch entry {entry.name}: {exc}") from exc
                    if stat.S_ISLNK(st.st_mode) or entry.is_symlink():
                        raise AutopilotError(f"unsafe rescope epoch symlink: {entry.name}")
                    if not stat.S_ISDIR(st.st_mode):
                        raise AutopilotError(f"unsafe rescope epoch non-directory: {entry.name}")
                    if st.st_uid != os.geteuid():
                        raise AutopilotError(f"unsafe rescope epoch owner: {entry.name}")
                    if (st.st_mode & 0o777) != 0o700:
                        raise AutopilotError(f"unsafe rescope epoch mode: {entry.name}")
                    if entry.name != current_epoch:
                        has_prior_epoch = True
    except (KeyboardInterrupt, SystemExit, AutopilotError):
        raise
    except OSError as exc:
        raise AutopilotError(f"cannot scan state_root: {exc}") from exc

    if has_prior_epoch:
        checkpoint = _load_accepted_checkpoint(state_root)
        if checkpoint is None or not _accepted_checkpoint_matches_repository(checkpoint, repository):
            raise AutopilotError("prior epoch requires matching accepted checkpoint")


_validate_rescope_epoch_startup = validate_rescope_epoch_startup


def prepare_rescope_attempt(
    state_root: Path,
    repository: object,
) -> tuple[Path, int, dict[str, object] | None]:
    """Prepare the next validated rescope attempt and recovery context."""
    if not isinstance(state_root, Path):
        raise AutopilotError("state_root must be a Path")
    epoch_dir = ensure_rescope_epoch_dir(state_root, repository)
    latest = load_latest_rescope_receipt(epoch_dir, repository)
    if latest is None:
        next_attempt = 1
        recovery = None
    else:
        attempt = latest.get("attempt")
        if latest.get("terminal") is True or attempt == 3:
            raise AutopilotError("terminal rescope receipt reached; cannot advance")
        if attempt not in (1, 2):
            raise AutopilotError(f"invalid latest rescope attempt: {attempt!r}")
        next_attempt = attempt + 1
        recovery = dict(build_rescope_recovery_context(latest, repository))
    if rescope_intent_exists(epoch_dir, next_attempt):
        raise AutopilotError(
            f"ambiguous: rescope intent already exists for attempt {next_attempt}"
        )
    return epoch_dir, int(next_attempt), recovery


def publish_validated_rescope_outcome(
    root: Path,
    epoch_dir: Path,
    attempt: int,
    result: object,
    expected_repository: object,
) -> dict[str, object]:
    """Publish a validated BLOCKED/RESCOPE outcome receipt to epoch_dir."""
    if not isinstance(root, Path):
        raise AutopilotError("root must be a Path")
    if not isinstance(epoch_dir, Path):
        raise AutopilotError("epoch_dir must be a Path")
    if type(attempt) is not int or isinstance(attempt, bool) or attempt not in (1, 2, 3):
        raise AutopilotError("attempt must be an integer in 1..3")
    if not isinstance(result, dict):
        raise AutopilotError("result must be a mapping")

    result_keys = set(result)
    if result_keys == RESCOPE_RECEIPT_KEYS:
        pass
    elif result_keys == (RESCOPE_RECEIPT_KEYS | {"advisories"}):
        if result.get("advisories") != []:
            raise AutopilotError("advisories must be an empty list")
    else:
        raise AutopilotError("result keys do not match RESCOPE_RECEIPT_KEYS")

    projected_result = {k: result[k] for k in RESCOPE_RESULT_KEYS_ORDER}

    if isinstance(expected_repository, Path) or not isinstance(expected_repository, dict):
        raise AutopilotError("expected_repository must be a mapping")
    if isinstance(expected_repository.get("assignment_scope"), AssignmentScope):
        normalized_expected_repo = validate_accepted_repository(expected_repository)
    else:
        normalized_expected_repo = _validate_persisted_repository(expected_repository)

    current_repo = validate_repository(root, require_clean=True)
    normalized_current_repo = validate_accepted_repository(current_repo)
    if normalized_current_repo != normalized_expected_repo:
        raise AutopilotError("current repository facts do not match expected repository facts")

    if not rescope_intent_exists(epoch_dir, attempt):
        raise AutopilotError(f"rescope intent does not exist for attempt {attempt}")

    latest = load_latest_rescope_receipt(epoch_dir, normalized_expected_repo)
    if attempt == 1:
        if latest is not None:
            raise AutopilotError("attempt 1 requires no prior rescope receipt")
    else:
        if latest is None:
            raise AutopilotError(f"attempt {attempt} requires prior rescope receipt")
        if latest.get("attempt") != attempt - 1:
            raise AutopilotError(f"attempt {attempt} requires prior receipt for attempt {attempt - 1}")
        if latest.get("terminal") is not False:
            raise AutopilotError("prior rescope receipt must be non-terminal")

    validate_rescope_checkpoint(projected_result, normalized_expected_repo)

    if attempt == 1:
        publish_initial_rescope_receipt(epoch_dir, projected_result, normalized_expected_repo)
    else:
        publish_next_rescope_receipt(epoch_dir, projected_result, normalized_expected_repo)

    try:
        reloaded = load_latest_rescope_receipt(epoch_dir, normalized_expected_repo)
        if reloaded is None:
            raise AutopilotError("ambiguous: reloaded rescope receipt is missing")
        if reloaded.get("attempt") != attempt:
            raise AutopilotError("ambiguous: reloaded rescope receipt attempt mismatch")
        expected_terminal = (attempt == 3)
        if reloaded.get("terminal") is not expected_terminal:
            raise AutopilotError("ambiguous: reloaded rescope receipt terminal mismatch")
        if reloaded.get("result") != projected_result:
            raise AutopilotError("ambiguous: reloaded rescope receipt result mismatch")
        if reloaded.get("repository") != normalized_expected_repo:
            raise AutopilotError("ambiguous: reloaded rescope receipt repository mismatch")
        live_repo = validate_repository(root, require_clean=True)
        live_normalized = validate_accepted_repository(live_repo)
        if live_normalized != normalized_expected_repo:
            raise AutopilotError("ambiguous: live repository drifted postwrite")
        return reloaded
    except (KeyboardInterrupt, SystemExit):
        raise
    except AutopilotError as exc:
        if str(exc).startswith("ambiguous"):
            raise
        raise AutopilotError(f"ambiguous: {exc}") from exc
    except Exception as exc:
        raise AutopilotError(f"ambiguous: {exc}") from exc


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


def _is_committed_halt_receipt(result: object) -> bool:
    if not isinstance(result, dict) or set(result) != RESULT_KEYS:
        return False
    if result.get("schema_version") != "1.0":
        return False
    if result.get("state") != "BLOCKED":
        return False
    if result.get("disposition") != "HALT" or result.get("category") != "EXTERNAL_GATE":
        return False
    summary = result.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return False
    commit = result.get("commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        return False
    next_action = result.get("next_action")
    if next_action is not None and not (isinstance(next_action, str) and next_action.strip()):
        return False
    blockers = result.get("blockers")
    if (
        not isinstance(blockers, list)
        or not (1 <= len(blockers) <= 20)
        or any(not isinstance(item, str) or not item.strip() for item in blockers)
    ):
        return False
    worker_run_id = result.get("gemini_worker_run_id")
    reviewer_run_id = result.get("gemini_reviewer_run_id")
    proposal_sha256 = result.get("proposal_sha256")
    if (
        not isinstance(worker_run_id, str)
        or not PROPOSAL_RUN_ID.fullmatch(worker_run_id)
        or not isinstance(reviewer_run_id, str)
        or not PROPOSAL_RUN_ID.fullmatch(reviewer_run_id)
        or not isinstance(proposal_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", proposal_sha256)
    ):
        return False
    return True


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
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise AutopilotError("Gemini Worker snapshot is incomplete")
    if assignment_scope is not None:
        try:
            validate_task_scope(assignment_scope, task)
        except AssignmentScopeError:
            raise AutopilotError("assignment scope validation failed") from None
    worker_result = validate_proposal_result(
        unique_json(worker_result_text), task, sources, "worker", enforce_raw_transport=False
    )
    if assignment_scope is not None:
        try:
            validate_changed_paths(
                assignment_scope,
                [change["path"] for change in worker_result["changes"]],
            )
        except AssignmentScopeError:
            raise AutopilotError("assignment scope validation failed") from None
    validate_proposal_result(reviewer_result, task, sources, "verifier", enforce_raw_transport=False)
    proposal_sha256 = file_sha256(worker_result_path)
    if (worker_snapshot.get("source_head") != source_head
            or reviewer_snapshot.get("source_head") != source_head
            or reviewer_snapshot.get("task") != task
            or reviewer_snapshot.get("sources") != sources
            or reviewer_snapshot.get("proposal") != worker_result_text
            or worker_status.get("run_id") != result["gemini_worker_run_id"]
            or worker_status.get("role") != "worker"
            or worker_status.get("state") != "PROPOSAL_READY"
            or worker_status.get("model") != expected_role_model("worker", task_id)
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


def validate_control_changes_are_proposed(
    before_controls: object,
    after_controls: object,
    proposal: object,
) -> None:
    """Allow a reviewed control update, but never an unbound control drift."""
    if not isinstance(before_controls, dict) or not isinstance(after_controls, dict):
        raise AutopilotError("automation control snapshot is invalid")
    changed = sorted(
        path for path in set(before_controls) | set(after_controls)
        if before_controls.get(path) != after_controls.get(path)
    )
    if not changed:
        return
    if not isinstance(proposal, dict) or not isinstance(proposal.get("changes"), list):
        raise AutopilotError("changed automation control has no bound proposal")
    proposal_paths = {
        change.get("path") for change in proposal["changes"]
        if isinstance(change, dict) and isinstance(change.get("path"), str)
    }
    unexpected = [path for path in changed if path not in proposal_paths]
    if unexpected:
        raise AutopilotError("a protected automation control changed outside the bound proposal")


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
    ref_drift = after["protected_refs"] != before["protected_refs"]
    committed_halt = _is_committed_halt_receipt(result)
    if ref_drift and not committed_halt:
        raise AutopilotError("a protected main/origin/candidate ref changed during the cycle")
    if committed_halt and not ref_drift:
        raise AutopilotError("committed HALT requires an actual protected ref change")
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
        validate_control_changes_are_proposed(
            before["control_blobs"], after["control_blobs"], proposal,
        )
    elif result["commit"] is not None:
        raise AutopilotError("cycle reported a commit without changing integration HEAD")
    elif after["control_blobs"] != before["control_blobs"]:
        raise AutopilotError("a protected automation control changed without a bound commit")
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
    checkpoint = _load_accepted_checkpoint(state_root)
    if checkpoint is None:
        return None
    repository = validate_repository(root)
    if not _accepted_checkpoint_matches_repository(checkpoint, repository):
        return None
    result = checkpoint.get("result")
    if not isinstance(result, dict):
        return None
    if continuation_hint(result, current_head) is None:
        return None
    return result


def _validate_startup_file(
    root: Path,
    startup_file: Path,
    expected_startup: PreparationStartup,
    expected_bytes: bytes,
) -> None:
    try:
        st = startup_file.stat(follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or os.path.islink(startup_file):
            raise AutopilotError("preparation startup file is unsafe or linked")
        if startup_file.resolve() != startup_file:
            raise AutopilotError("preparation startup path is not canonical")
        if st.st_size > 4096:
            raise AutopilotError("preparation startup file exceeds limit")
        raw_text = bounded_file_text(root, startup_file, 4096, "preparation startup snapshot")
        raw_bytes = raw_text.encode("utf-8")
    except AutopilotError:
        raise
    except (ProposalError, OSError, UnicodeError) as exc:
        raise AutopilotError("preparation startup file verification failed") from exc

    if len(raw_bytes) > 4096:
        raise AutopilotError("preparation startup file exceeds limit")
    if raw_bytes != expected_bytes:
        raise AutopilotError("preparation startup file content drifted")
    try:
        parsed = unique_json(raw_text)
        observed = PreparationStartup.from_mapping(parsed)
        assert_same_startup(expected_startup, observed)
    except (PreparationConfigError, ValueError, ProposalError) as exc:
        raise AutopilotError("preparation startup identity drifted") from exc
    if observed.fingerprint != expected_startup.fingerprint:
        raise AutopilotError("preparation startup fingerprint drifted")


def _validate_legacy_preparation_startup(root: Path, state_root: Path) -> None:
    try:
        from scripts.two_site_preparation_runtime_gate import validate_legacy_preparation_startup
    except ModuleNotFoundError:
        from two_site_preparation_runtime_gate import validate_legacy_preparation_startup  # type: ignore[no-redef]
    validate_legacy_preparation_startup(root, state_root)


def prompt_for_cycle(
    root: Path,
    cycle: int,
    before: dict[str, object],
    recovery: dict[str, object] | None = None,
    continuation: dict | None = None,
    preparation_startup: PreparationStartup | None = None,
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
    if preparation_startup is not None:
        if type(preparation_startup) is not PreparationStartup:
            raise AutopilotError("invalid preparation startup")
        context["preparation_startup"] = preparation_startup.to_mapping()
        context["preparation_startup_sha256"] = preparation_startup.fingerprint
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


def elapsed_seconds(started_at: float) -> float:
    """Return a non-negative monotonic duration for source-free cycle telemetry."""
    return round(max(0.0, time.monotonic() - started_at), 3)


def run_cycle(
    root: Path,
    codex: Path,
    state_root: Path,
    cycle: int,
    timeout: int,
    recovery: dict[str, object] | None = None,
    continuation: dict | None = None,
    *,
    preparation_route: str = "one_shot",
    before_model: Callable[[dict[str, object]], None] | None = None,
) -> tuple[Path, dict]:
    if not isinstance(preparation_route, str) or preparation_route not in {"one_shot", "legacy"}:
        raise AutopilotError(f"invalid preparation route: {preparation_route!r}")
    global _ACTIVE_PROCESS
    heartbeat_stop: threading.Event | None = None
    heartbeat_thread: threading.Thread | None = None
    primary_exception: BaseException | None = None
    phase_seconds: dict[str, float | None] = {
        "repository_validation": None,
        "task_context": None,
        "codex_readiness": None,
        "coordinator": None,
        "post_coordinator_validation": None,
    }
    validation_started_at = time.monotonic()
    before = validate_repository(root, require_clean=True)
    phase_seconds["repository_validation"] = elapsed_seconds(validation_started_at)
    scope = before.get("assignment_scope")
    if not isinstance(scope, AssignmentScope):
        raise AutopilotError("invalid or missing startup assignment scope")
    context_started_at = time.monotonic()
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = state_root / "runs" / f"cycle-{cycle:04d}-{timestamp}-{os.getpid()}"
    if not RUN_ID.fullmatch(run_dir.name) or os.path.isabs(run_dir.name):
        raise AutopilotError(f"invalid cycle run ID: {run_dir.name}")
    run_dir.mkdir(mode=0o700)
    resolved_root = root.resolve(strict=True)
    resolved_run_dir = run_dir.resolve()
    if (
        resolved_run_dir != run_dir
        or not run_dir.is_dir()
    ):
        raise AutopilotError("unsafe run directory")
    try:
        resolved_run_dir.relative_to(resolved_root)
    except ValueError:
        raise AutopilotError("run directory outside canonical root")
    startup = PreparationStartup(1, preparation_route, run_dir.name, str(before["head"]), scope.sha256)
    if run_dir.name != startup.cycle_id:
        raise AutopilotError("run directory does not match startup cycle ID")
    startup_bytes = json.dumps(
        startup.to_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    startup_file = run_dir / "preparation-startup.json"
    if os.path.lexists(startup_file):
        raise AutopilotError("preparation startup target already exists")
    try:
        _atomic_new_json(startup_file, startup.to_mapping(), 4096)
    except BrokerError as exc:
        raise AutopilotError(f"cannot publish preparation startup: {exc}") from exc
    prompt = prompt_for_cycle(
        root, cycle, before, recovery=recovery, continuation=continuation,
        preparation_startup=startup,
    )
    (run_dir / "prompt.txt").write_text(prompt)
    phase_seconds["task_context"] = elapsed_seconds(context_started_at)
    readiness_started_at = time.monotonic()
    codex = codex.resolve(strict=True)
    identity = codex_identity(codex, run_dir)
    phase_seconds["codex_readiness"] = elapsed_seconds(readiness_started_at)
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
        "preparation_startup": startup.to_mapping(),
        "preparation_startup_sha256": startup.fingerprint,
        "output_limit_bytes": MAX_CODEX_OUTPUT,
        "timeout_seconds": timeout,
        "controller_phase_seconds": phase_seconds,
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
    environment["TWO_SITE_PREPARATION_STARTUP_SHA256"] = startup.fingerprint
    coordinator_started_at: float | None = None
    post_validation_started_at: float | None = None
    try:
        if before_model is not None:
            before_model(before)
        coordinator_started_at = time.monotonic()
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
        phase_seconds["coordinator"] = elapsed_seconds(coordinator_started_at)
        post_validation_started_at = time.monotonic()
        if process.returncode:
            raise AutopilotError(f"Codex CLI exited with status {process.returncode}")
        if file_sha256(codex) != identity["sha256"]:
            raise AutopilotError("Codex CLI identity changed during the cycle")
        if codex_runtime_identity(codex) != identity["runtime"]:
            raise AutopilotError("Codex runtime changed during the cycle")
        _validate_startup_file(root, startup_file, startup, startup_bytes)
        result_path = run_dir / "result.json"
        raw_result = unique_json(
            bounded_file_text(root, result_path, MAX_RESULT_BYTES, "Codex cycle result")
        )
        try:
            validated_receipt = validate_result(raw_result)
            advisories: list[str] = []
        except AutopilotError as exc:
            if str(exc) == "non-blocked state cannot report blockers":
                validated_receipt, advisories = recover_committed_advisory_result(raw_result, exc)
            elif (
                str(exc) == "HALT requires null commit and Gemini acceptance evidence"
                and _is_committed_halt_receipt(raw_result)
            ):
                validated_receipt = raw_result
                advisories = []
            else:
                raise
        result = validate_transition(root, before, validated_receipt)
        if result["state"] == "CONTINUE":
            final_repository = validate_repository(root, require_clean=True)
            if final_repository["protected_refs"] != before["protected_refs"]:
                raise AutopilotError("protected_refs changed before checkpoint publication")
            if final_repository["assignment_scope"] != before["assignment_scope"]:
                raise AutopilotError("assignment_scope changed before checkpoint publication")
            publish_accepted_checkpoint(state_root, result, final_repository)
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
        phase_seconds["post_coordinator_validation"] = elapsed_seconds(post_validation_started_at)
        meta["controller_phase_seconds"] = phase_seconds
        write_json(run_dir / "status.json", meta)
        return run_dir, result
    except BaseException as exc:
        primary_exception = exc
        meta.update(
            state="FAILED",
            error=type(exc).__name__ + ": " + str(exc),
            finished_at=time.time(),
            duration_seconds=round(time.time() - float(meta["started_at"]), 3),
        )
        write_json(run_dir / "status.json", meta)
        raise
    finally:
        if coordinator_started_at is not None and phase_seconds["coordinator"] is None:
            phase_seconds["coordinator"] = elapsed_seconds(coordinator_started_at)
        if (post_validation_started_at is not None
                and phase_seconds["post_coordinator_validation"] is None):
            phase_seconds["post_coordinator_validation"] = elapsed_seconds(post_validation_started_at)
        # A phase may be unavailable only when the cycle failed before that phase.
        # Metrics accepts only complete timing vectors, so do not publish a partial
        # breakdown as if it were comparable with successful cycles.
        if all(value is not None for value in phase_seconds.values()):
            meta["controller_phase_seconds"] = {
                key: float(value) for key, value in phase_seconds.items()
            }
        else:
            meta.pop("controller_phase_seconds", None)
        try:
            write_json(run_dir / "status.json", meta)
        except OSError:
            pass
        if heartbeat_stop is not None:
            heartbeat_stop.set()
        try:
            if heartbeat_thread is not None:
                heartbeat_thread.join()
        except BaseException:
            if primary_exception is None:
                raise
        finally:
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


def run_loop(
    root: Path,
    codex: Path,
    max_cycles: int,
    timeout: int,
    once: bool,
    *,
    preparation_route: str = "one_shot",
    quota_wait_enabled: bool = False,
) -> int:
    if type(quota_wait_enabled) is not bool:
        raise AutopilotError("quota_wait_enabled must be an exact bool")
    if not isinstance(preparation_route, str) or preparation_route not in {"one_shot", "legacy"}:
        raise AutopilotError(f"invalid preparation route: {preparation_route!r}")
    root = root.resolve(strict=True)
    state_root = root / ".local/two-site-refactor/autopilot"
    if not state_root.exists():
        state_root.parent.mkdir(parents=True, exist_ok=True)
        try:
            state_root.mkdir(mode=0o700)
        except OSError:
            pass
    if state_root.resolve() != state_root:
        raise AutopilotError("autopilot state directory cannot be a symlink")
    st = state_root.stat(follow_symlinks=False)
    if not stat.S_ISDIR(st.st_mode):
        raise AutopilotError("state_root is not a directory")
    if st.st_uid != os.geteuid():
        raise AutopilotError("state_root directory owner must be process euid")
    if (st.st_mode & 0o777) != 0o700:
        raise AutopilotError("state_root directory mode must be 0700")
    (state_root / "runs").mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = state_root / "autopilot.lock"
    with exclusive_supervisor_lock(lock_path):
        stop_path = state_root / "STOP"
        if os.path.lexists(stop_path):
            if not stop_path.exists():
                raise AutopilotError("STOP path is a broken symlink")
            write_supervisor_state(state_root, "STOPPED", cycle=1)
            return 0
        if preparation_route == "legacy":
            _validate_legacy_preparation_startup(root, state_root)
        try:
            repo_facts = validate_repository(root, require_clean=True)
            expected_repository = repo_facts
            validate_rescope_epoch_startup(state_root, repo_facts)
            epoch_dir, next_attempt, startup_recovery = prepare_rescope_attempt(state_root, expected_repository)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            if (
                quota_wait_enabled
                and type(exc) is AutopilotError
                and str(exc).startswith("ambiguous: rescope intent already exists")
            ):
                try:
                    head = repo_facts.get("head") if type(repo_facts) is dict else None
                    if type(head) is not str or len(head) != 40 or not bool(re.fullmatch(r"[0-9a-f]{40}", head)):
                        raise AutopilotError("repository facts head is missing or not a lowercase 40hex sha")
                    try:
                        from scripts.two_site_quota_wait_recovery import (
                            recover_durable_initial_wait_locked,
                        )
                    except ModuleNotFoundError:
                        from two_site_quota_wait_recovery import (
                            recover_durable_initial_wait_locked,
                        )
                    try:
                        from scripts.two_site_provider_broker import CYCLE_RUN_RE
                    except ModuleNotFoundError:
                        from two_site_provider_broker import CYCLE_RUN_RE

                    recovered = recover_durable_initial_wait_locked(root)
                    if recovered is None:
                        raise AutopilotError("no durable quota wait record recovered")
                    if type(recovered) is not tuple or len(recovered) != 2:
                        raise AutopilotError("recovered quota wait result must be an exact tuple of length 2")
                    cid, record = recovered
                    if type(cid) is not str or not bool(CYCLE_RUN_RE.fullmatch(cid)):
                        raise AutopilotError("recovered controller_run_id is invalid")
                    if type(record) is not dict:
                        raise AutopilotError("recovered quota wait record must be an exact dict")
                    if record.get("state") != "WAITING_QUOTA":
                        raise AutopilotError("recovered quota wait record state must be WAITING_QUOTA")
                    if record.get("controller_run_id") != cid:
                        raise AutopilotError("recovered quota wait record controller_run_id mismatch")
                    req_id = record.get("request_id")
                    if type(req_id) is not str or len(req_id) == 0:
                        raise AutopilotError("recovered quota wait record request_id must be a nonempty str")
                    sched = record.get("schedule")
                    if type(sched) is not dict:
                        raise AutopilotError("recovered quota wait record schedule must be an exact dict")
                    if os.path.lexists(stop_path):
                        if not stop_path.exists():
                            raise AutopilotError("STOP path is a broken symlink")
                        write_supervisor_state(state_root, "STOPPED", cycle=1)
                        return 0
                    current_repo = validate_repository(root, require_clean=True)
                    if type(current_repo) is not dict or current_repo != repo_facts:
                        raise AutopilotError("repository facts drifted or invalid during quota wait recovery")
                    if os.path.lexists(stop_path):
                        if not stop_path.exists():
                            raise AutopilotError("STOP path is a broken symlink")
                        write_supervisor_state(state_root, "STOPPED", cycle=1)
                        return 0
                    run_dir = state_root / "runs" / cid
                    write_supervisor_state(
                        state_root,
                        "WAITING_QUOTA",
                        cycle=1,
                        evidence=str(run_dir),
                        head=head,
                        request_id=req_id,
                        schedule=sched,
                        summary="Validated terminal quota; awaiting explicit resume gate.",
                        blockers=[],
                    )
                    return 2
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as recovery_exc:
                    blocker = str(recovery_exc)[:512] or type(recovery_exc).__name__
                    write_supervisor_state(state_root, "BLOCKED", cycle=1, blockers=[blocker])
                    return 2
            blocker = str(exc)[:512] or type(exc).__name__
            write_supervisor_state(state_root, "BLOCKED", cycle=1, blockers=[blocker])
            return 2
        consecutive_rescopes = next_attempt - 1
        recovery_context = startup_recovery
        retryable_failures = 0
        previous_success = None
        for cycle in range(1, max_cycles + 1):
            if os.path.lexists(stop_path):
                if not stop_path.exists():
                    raise AutopilotError("STOP path is a broken symlink")
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
            callback_entered = False
            callback_published = False

            def before_model_callback(before: dict[str, object]) -> None:
                nonlocal callback_entered, callback_published
                callback_entered = True
                norm_expected = validate_accepted_repository(expected_repository)
                norm_before = validate_accepted_repository(before)
                current_repo = validate_repository(root, require_clean=True)
                norm_current = validate_accepted_repository(current_repo)
                if norm_before != norm_expected or norm_current != norm_expected:
                    raise AutopilotError("repository facts do not match expected repository facts before model invocation")
                publish_rescope_intent(epoch_dir, next_attempt)
                callback_published = True

            try:
                continuation_args = ({"continuation": previous_success}
                                     if continuation_hint(previous_success, cycle_start_head) is not None else {})
                cycle_kwargs: dict[str, object] = {}
                if preparation_route == "legacy":
                    cycle_kwargs["preparation_route"] = preparation_route
                run_dir, result = run_cycle(
                    root, codex, state_root, cycle, timeout, recovery=recovery_context,
                    before_model=before_model_callback,
                    **continuation_args,
                    **cycle_kwargs,
                )
                if not (callback_entered and callback_published and rescope_intent_exists(epoch_dir, next_attempt)):
                    write_supervisor_state(
                        state_root, "BLOCKED", cycle=cycle,
                        head=git(root, "rev-parse", "HEAD"),
                        blockers=[
                            "run_cycle returned without before_model callback invocation"
                            if not callback_entered
                            else "run_cycle returned without verified durable rescope intent"
                        ],
                    )
                    return 2
                if quota_wait_enabled:
                    try:
                        from scripts.two_site_quota_supervisor_bridge import (
                            accept_cycle_quota_disposition_locked,
                        )
                    except ModuleNotFoundError:
                        from two_site_quota_supervisor_bridge import (
                            accept_cycle_quota_disposition_locked,
                        )
                    record = accept_cycle_quota_disposition_locked(
                        root, run_dir, result, enabled=True,
                    )
                    if record is not None:
                        if type(record) is not dict or record.get("state") != "WAITING_QUOTA":
                            raise AutopilotError("invalid quota wait admission record")
                        write_supervisor_state(
                            state_root,
                            "WAITING_QUOTA",
                            cycle=cycle,
                            evidence=str(run_dir),
                            head=result["head_after"],
                            request_id=record["request_id"],
                            schedule=record["schedule"],
                            summary="Validated terminal quota; awaiting explicit resume gate.",
                            blockers=[],
                        )
                        return 2
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
                    try:
                        expected_repository = validate_repository(root, require_clean=True)
                        epoch_dir, next_attempt, startup_recovery = prepare_rescope_attempt(
                            state_root, expected_repository
                        )
                        consecutive_rescopes = next_attempt - 1
                        recovery_context = startup_recovery
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception as exc:
                        write_supervisor_state(
                            state_root, "BLOCKED", cycle=cycle,
                            head=result["head_after"],
                            blockers=[type(exc).__name__ + ": " + str(exc)],
                        )
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
                    try:
                        rescope_receipt = publish_validated_rescope_outcome(
                            root, epoch_dir, next_attempt, result, expected_repository
                        )
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception as exc:
                        write_supervisor_state(
                            state_root, "BLOCKED", cycle=cycle,
                            head=result["head_after"],
                            blockers=[type(exc).__name__ + ": " + str(exc)],
                        )
                        return 2
                    attempt = rescope_receipt.get("attempt")
                    consecutive_rescopes = int(attempt)
                    if rescope_receipt.get("terminal") is True or consecutive_rescopes >= MAX_CONSECUTIVE_RESCOPES:
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
                    recovery_context = build_rescope_recovery_context(rescope_receipt, expected_repository)
                    try:
                        epoch_dir, next_attempt, _ = prepare_rescope_attempt(
                            state_root, expected_repository
                        )
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception as exc:
                        write_supervisor_state(
                            state_root, "BLOCKED", cycle=cycle,
                            head=result["head_after"],
                            blockers=[type(exc).__name__ + ": " + str(exc)],
                        )
                        return 2
                    consecutive_rescopes = next_attempt - 1
                    continue

                prune_runs(state_root)
                write_supervisor_state(
                    state_root, "BLOCKED", cycle=cycle,
                    evidence=str(run_dir), head=result["head_after"],
                    summary=result["summary"], next_action=result["next_action"],
                    blockers=result["blockers"],
                    head_after=result["head_after"],
                    head_changed=result["head_changed"],
                    commit=result["commit"],
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
                if callback_entered:
                    write_supervisor_state(
                        state_root, "BLOCKED", cycle=cycle, head=current_head,
                        blockers=[type(exc).__name__ + ": " + str(exc)], dirty=dirty,
                        ambiguous_change=ambiguous_change,
                    )
                    return 2
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
    parser.add_argument(
        "--preparation-route",
        choices=("one_shot", "legacy"),
        default="one_shot",
    )
    args = parser.parse_args()
    if args.codex is None:
        args.codex = args.root.resolve() / ".local/tools/codex-0.156.1-0b2e9301d6100ddd/codex"
    if not 1 <= args.max_cycles <= DEFAULT_MAX_CYCLES or not 300 <= args.timeout <= 14_400:
        raise SystemExit("invalid max-cycles or timeout")
    for handled in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(handled, signal_handler)
    if args.check:
        state = validate_repository(args.root)
        if args.preparation_route == "legacy":
            resolved_root = args.root.resolve(strict=True)
            state_root = resolved_root / ".local/two-site-refactor/autopilot"
            lock_path = state_root / "autopilot.lock"
            with exclusive_supervisor_lock(lock_path):
                _validate_legacy_preparation_startup(resolved_root, state_root)
        with tempfile.TemporaryDirectory(prefix="two-site-autopilot-check-") as temporary:
            identity = codex_identity(args.codex, Path(temporary))
        print(json.dumps({
            "state": "READY", "branch": BRANCH, "head": state["head"],
            "codex": identity,
            "assignment_scope": assignment_scope_context(state.get("assignment_scope")),
        }))
        return 0
    try:
        loop_kwargs: dict[str, object] = {}
        if args.preparation_route == "legacy":
            loop_kwargs["preparation_route"] = args.preparation_route
        return run_loop(
            args.root, args.codex, args.max_cycles, args.timeout, args.once, **loop_kwargs
        )
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"state": "BLOCKED", "reason": str(exc)}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
