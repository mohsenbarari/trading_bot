#!/usr/bin/env python3
"""Bounded Antigravity proposals. The configured coordinator accepts/applies them.

The controller writes ignored run artifacts only; it does not apply generated code,
change Git refs/Stage state or deploy. The CLI uses plan/sandbox mode and a deny hook;
tool attempts invalidate its result. Each invocation is a fresh session.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
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
from datetime import datetime, timezone
from typing import Callable, Iterator, Mapping

try:
    from scripts.two_site_ai_config import AI_CONFIG_REL, AIConfigError, load_ai_config
except ImportError:
    from two_site_ai_config import AI_CONFIG_REL, AIConfigError, load_ai_config

try:
    from scripts.two_site_retry_syntax_hint import extract_syntax_hint, validate_retry_hint
except ImportError:
    from two_site_retry_syntax_hint import extract_syntax_hint, validate_retry_hint

try:
    from scripts.two_site_quota_evidence import classify_gemini_quota_stream
except ImportError:
    from two_site_quota_evidence import classify_gemini_quota_stream

try:
    from scripts.two_site_cli_artifact import prepare_disposable_cli
except ImportError:
    from two_site_cli_artifact import prepare_disposable_cli

try:
    from scripts.two_site_worker_model_routing import (
        load_worker_routing_policy,
        select_worker_model_route,
    )
except ImportError:
    from two_site_worker_model_routing import (
        load_worker_routing_policy,
        select_worker_model_route,
    )

try:
    from scripts.two_site_assignment_scope import (
        AssignmentFact,
        AssignmentScopeError,
        load_expected_assignment_scope,
        validate_task_scope,
    )
except ImportError:
    from two_site_assignment_scope import (
        AssignmentFact,
        AssignmentScopeError,
        load_expected_assignment_scope,
        validate_task_scope,
    )

try:
    from scripts.two_site_proposal_transport import (
        TransportPolicyError,
        build_changes_schema,
        classify_source,
        validate_change_mode,
    )
except ImportError:
    from two_site_proposal_transport import (
        TransportPolicyError,
        build_changes_schema,
        classify_source,
        validate_change_mode,
    )

try:
    from scripts.two_site_correction_delta import (
        CorrectionDeltaError,
        build_candidate_sources,
        rebase_correction_result,
    )
except ImportError:
    from two_site_correction_delta import (
        CorrectionDeltaError,
        build_candidate_sources,
        rebase_correction_result,
    )

try:
    from scripts.two_site_pre_review_receipt import validate_test_receipt
except ImportError:
    from two_site_pre_review_receipt import validate_test_receipt

try:
    from scripts.two_site_opus_fallback_receipt import (
        OpusReceiptValidationError,
        validate_opus_failure_receipt,
    )
except ImportError:
    from two_site_opus_fallback_receipt import (
        OpusReceiptValidationError,
        validate_opus_failure_receipt,
    )

try:
    from scripts.two_site_readiness_contract import (
        _MODEL_CONTEXT_CODES,
        _READINESS_ISSUE_DETAILS,
        _TASK_BOUNDARY_CODES,
        _TASK_BOUNDARY_DESCRIPTIONS,
    )
except ImportError:
    from two_site_readiness_contract import (
        _MODEL_CONTEXT_CODES,
        _READINESS_ISSUE_DETAILS,
        _TASK_BOUNDARY_CODES,
        _TASK_BOUNDARY_DESCRIPTIONS,
    )


try:
    from scripts.two_site_generation_completion import (
        POLICY as GENERATION_COMPLETION_POLICY, GenerationCompletionError,
        GenerationRecoveryPolicy, capture_generation_process,
        normal_observations, validate_observations,
    )
except ImportError:
    from two_site_generation_completion import (
        POLICY as GENERATION_COMPLETION_POLICY, GenerationCompletionError,
        GenerationRecoveryPolicy, capture_generation_process,
        normal_observations, validate_observations,
    )

ROOT = Path(__file__).resolve().parents[1]
MAX_CONTEXT = 160_000
MAX_OUTPUT = 2_000_000
STREAM_CHUNK_SIZE = 64 * 1024
GEMINI_RUNTIME_CONTRACT_REL = Path(
    "docs/refactor/two-server/templates/gemini-runtime-contract.json"
)
GEMINI_RUNTIME_REFERENCE_PATHS = (
    AI_CONFIG_REL,
    Path("docs/refactor/two-server/PROPOSAL_AUTOMATION.md"),
    Path("docs/refactor/two-server/ANTIGRAVITY_EXECUTION.md"),
    Path(".agents/skills/two-site-refactor/resources/AUTONOMOUS_ORCHESTRATION.md"),
    Path(".agents/skills/two-site-refactor/resources/TYPED_GATEWAY.md"),
)
RUNTIME_CONTRACT_KEYS = frozenset({
    "schema_version", "provider", "model", "effort", "worker_model", "worker_effort",
    "cli_version", "cli_sha256", "completion_policy",
})
VALID_EFFORTS = frozenset({"low", "medium", "high"})
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
DECORATION_KEYS = frozenset({"toolAction", "toolSummary"})
MAX_DECORATION_BYTES = 256
MAX_REPLACEMENTS = 32
MAX_REPLACEMENT_BYTES = 65536
MAX_REVIEW_TRANSPORT_BYTES = MAX_CONTEXT
AUTOPILOT_RUN_ID_ENV = "TWO_SITE_AUTOPILOT_RUN_ID"
MAINTENANCE_RUN_ID_ENV = "TWO_SITE_MAINTENANCE_RUN_ID"
MAX_AUTOPILOT_RUN_ID_LENGTH = 64
AUTOPILOT_RUN_ID_REGEX = re.compile(
    r"^cycle-[0-9]{4,8}-[0-9]{8}T[0-9]{6}Z-[0-9]{1,10}\Z"
)
MAINTENANCE_RUN_ID_REGEX = re.compile(
    r"^maint-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}\Z"
)
MAX_SESSION_FLOW_SYMBOLS = 48
MAX_SESSION_FLOW_MISSING = 16
MAX_SESSION_QUERY_HINTS = 24
MAX_SESSION_SOURCE_BYTES = 512_000
SESSION_OPERATIONS = frozenset({
    "execute", "scalar", "scalars", "stream", "stream_scalars", "get", "add",
    "delete", "merge", "flush", "commit", "rollback", "refresh", "expire",
    "expire_all", "begin_nested",
})
SESSION_RESULT_APIS = frozenset({
    "all", "fetchall", "fetchmany", "fetchone", "first", "mappings", "one",
    "one_or_none", "partitions", "scalar", "scalar_one", "scalar_one_or_none",
    "scalars", "tuples", "unique",
})
SESSION_CALL_SAFE_BUILTINS = frozenset({
    "bool", "callable", "getattr", "hasattr", "id", "isinstance", "repr", "setattr",
    "str", "type",
})
PROJECT_MODULE_ROOTS = frozenset({"api", "bot", "core", "scripts"})
LIVE_PATH_RE = re.compile(
    r"\b(?:live|real|authoritative)(?:[-_ ]+(?:execution|production|trade))?[-_ ]+path\b|"
    r"\b(?:live|real|authoritative)\s+(?:handler|query|route|call)\b|"
    r"\b(?:call|invoke|execute|exercise)s?\s+(?:the\s+)?(?:live|real|authoritative)\b",
    re.IGNORECASE,
)
SESSION_TEXT_RE = re.compile(
    r"\b(?:custom|fake|hybrid|stub)[-_ ]+session\b|\bsession[-_ ]+adapter\b|"
    r"\breus(?:e|ing)\b[^\n]{0,80}\bsession\b",
    re.IGNORECASE,
)
QUALIFIED_PROJECT_SYMBOL_RE = re.compile(
    r"\b((?:api|bot|core|scripts)(?:\.[A-Za-z_]\w*)+)\.([A-Za-z_]\w*)\b"
)


def validate_autopilot_run_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ProposalError("invalid autopilot run ID: empty or whitespace")
    if len(raw) > MAX_AUTOPILOT_RUN_ID_LENGTH:
        raise ProposalError("invalid autopilot run ID: exceeds length limit")
    if not AUTOPILOT_RUN_ID_REGEX.fullmatch(raw):
        raise ProposalError("invalid autopilot run ID: does not match canonical format")
    if SECRET.search(raw):
        raise ProposalError("invalid autopilot run ID: credential-like content")
    return raw


def controller_run_id_from_environment(environment: Mapping[str, str]) -> str | None:
    """Keep maintenance provenance distinct from real Supervisor cycle IDs."""
    cycle = environment.get(AUTOPILOT_RUN_ID_ENV)
    maintenance = environment.get(MAINTENANCE_RUN_ID_ENV)
    if AUTOPILOT_RUN_ID_ENV in environment and MAINTENANCE_RUN_ID_ENV in environment:
        raise ProposalError("ambiguous controller run ID environment")
    if maintenance is None:
        return validate_autopilot_run_id(cycle)
    if (
        not isinstance(maintenance, str)
        or len(maintenance) > MAX_AUTOPILOT_RUN_ID_LENGTH
        or not MAINTENANCE_RUN_ID_REGEX.fullmatch(maintenance)
        or SECRET.search(maintenance)
    ):
        raise ProposalError("invalid maintenance run ID")
    return maintenance


class ProposalError(ValueError):
    pass


_MAX_BOUNDARY_PATH_BYTES = 256


class _Fact(dict):
    def __call__(self) -> dict[str, str | None]:
        return dict(self)


class _TaskBoundaryError(ProposalError):
    CODES = _TASK_BOUNDARY_CODES
    _CODES = _TASK_BOUNDARY_CODES

    def __init__(
        self,
        code: str,
        path: str | None = None,
        *args: object,
        **kwargs: object,
    ) -> None:
        if args or kwargs:
            raise ProposalError("extra metadata not permitted")
        if not isinstance(code, str) or type(code) is not str:
            raise ProposalError("code must be a string")
        if code not in _TASK_BOUNDARY_CODES:
            raise ProposalError(f"unknown task boundary code: {code!r}")
        if path is not None:
            if not isinstance(path, str) or type(path) is not str:
                raise ProposalError("path must be a string or None")
            if len(path.encode("utf-8")) > _MAX_BOUNDARY_PATH_BYTES:
                raise ProposalError("path exceeds maximum byte length")
            if "\0" in path:
                raise ProposalError("path must not contain NUL bytes")
            if "\\" in path:
                raise ProposalError("path must not contain backslashes")
            if path.startswith("/") or PurePosixPath(path).is_absolute() or (len(path) >= 2 and path[1] == ":" and path[0].isalpha()):
                raise ProposalError("path must be relative")
            parts = path.split("/")
            if any(part in {"", ".", ".."} for part in parts):
                raise ProposalError("path must not contain empty or dot segments")
            if SECRET.search(path):
                raise ProposalError("path must not contain credential-like text")
        self._code = code
        self._path = path
        desc = _TASK_BOUNDARY_DESCRIPTIONS.get(code)
        if not desc and code == "TASK_CREDENTIAL_DETECTED":
            desc = "credential-like content detected"
        if desc and path:
            msg = f"task boundary rejection [{code}]: {path} {desc}"
        elif desc:
            msg = f"task boundary rejection [{code}]: {desc}"
        elif path:
            msg = f"task boundary rejection [{code}]: {path}"
        else:
            msg = f"task boundary rejection [{code}]"
        super().__init__(msg)

    @property
    def code(self) -> str:
        return self._code

    @property
    def path(self) -> str | None:
        return self._path

    @property
    def fact(self) -> _Fact:
        return _Fact({"code": self._code, "path": self._path})

    def to_fact(self) -> dict[str, str | None]:
        return {"code": self._code, "path": self._path}


def _collect_model_readiness_facts(
    dependency_context: object,
) -> list[dict[str, str | None]]:
    if dependency_context is None:
        return []
    if not isinstance(dependency_context, dict) or type(dependency_context) is not dict:
        raise ProposalError("invalid dependency context")
    if not dependency_context.get("persistence_contract_requested"):
        return []

    raw_facts: list[dict[str, str | None]] = []

    omitted = dependency_context.get("omitted_model_path_count", 0)
    if type(omitted) is not int or isinstance(omitted, bool) or omitted < 0:
        raw_facts.append({"code": "MODEL_CONTEXT_INVALID", "path": None})
    elif omitted > 0:
        raw_facts.append({"code": "MODEL_CONTEXT_LIMIT_EXCEEDED", "path": None})

    missing = dependency_context.get("missing_full_model_paths", [])
    if not isinstance(missing, list) or type(missing) is not list:
        raise ProposalError("missing_full_model_paths must be a list")

    for item in missing:
        if not isinstance(item, str) or type(item) is not str:
            continue
        _TaskBoundaryError("TASK_FILE_NOT_FOUND", item)
        raw_facts.append({"code": "MODEL_CONTEXT_MISSING", "path": item})

    seen: set[tuple[str, str | None]] = set()
    unique_facts: list[dict[str, str | None]] = []
    for fact in raw_facts:
        key = (fact["code"], fact["path"])
        if key not in seen:
            seen.add(key)
            unique_facts.append({"code": fact["code"], "path": fact["path"]})

    unique_facts.sort(
        key=lambda item: (
            item["code"],
            0 if item["path"] is None else 1,
            item["path"] or "",
        )
    )
    return unique_facts


def _readiness_issue(
    code: str,
    path: str | None = None,
    *args: object,
    **kwargs: object,
) -> dict[str, str]:
    if args or kwargs:
        raise ProposalError("extra metadata not permitted")
    if not isinstance(code, str) or type(code) is not str:
        raise ProposalError("code must be a string")
    if code in _TASK_BOUNDARY_CODES:
        err = _TaskBoundaryError(code, path)
        detail = _READINESS_ISSUE_DETAILS[err.code]
        if err.path is None:
            return {"code": err.code, "detail": detail}
        return {"code": err.code, "path": err.path, "detail": detail}
    if code in _MODEL_CONTEXT_CODES:
        if path is not None:
            _TaskBoundaryError("TASK_FILE_NOT_FOUND", path)
        detail = _READINESS_ISSUE_DETAILS[code]
        if path is None:
            return {"code": code, "detail": detail}
        return {"code": code, "path": path, "detail": detail}
    raise ProposalError(f"unknown task boundary code: {code!r}")


def _normalize_readiness_issues(
    facts: list[dict[str, object]],
    *args: object,
    **kwargs: object,
) -> list[dict[str, str]]:
    if args or kwargs:
        raise ProposalError("extra metadata not permitted")
    if not isinstance(facts, list) or type(facts) is not list:
        raise ProposalError("facts must be a list")
    canonical_by_key: dict[tuple[str, str | None], dict[str, str]] = {}
    for item in facts:
        if not isinstance(item, dict) or type(item) is not dict:
            raise ProposalError("fact must be an exact dict")
        if set(item.keys()) != {"code", "path"}:
            raise ProposalError("fact keys must be exactly 'code' and 'path'")
        issue = _readiness_issue(item["code"], item["path"])
        key = (issue["code"], issue.get("path"))
        if key not in canonical_by_key:
            canonical_by_key[key] = issue
    sorted_issues = sorted(
        canonical_by_key.values(),
        key=lambda issue: (
            issue["code"],
            0 if issue.get("path") is None else 1,
            issue.get("path") or "",
        ),
    )
    return [dict(issue) for issue in sorted_issues[:16]]


class RetryableProviderSessionError(ProposalError):
    pass


class ForbiddenToolAttemptError(RetryableProviderSessionError):
    """Explicit non-finish tool or subagent evidence in a proposal-only run."""


class RetryableProviderOutputError(RetryableProviderSessionError):
    """A bounded-retry failure attributable only to one provider response."""


_RETRYABLE_OUTPUT_MESSAGES = frozenset({
    "incomplete response contract",
    "malformed response list",
    "plan-only or no-op response is not implementation",
    "invalid change shape",
    "mixed change content and replacements",
    "empty change content and replacements",
    "proposal transport policy violation",
    "new-file replacement is not supported",
    "replacements must be a list",
    "replacement item must be a dict with exactly 'old' and 'new'",
    "replacement old must be a nonempty string",
    "replacement new must be a string",
    "replacement old and new must differ",
    "aggregate replacement UTF-8 bytes exceed limit",
    "replacement spans overlap",
    "empty or unchanged file",
})


def _provider_output_is_retryable(exc: ProposalError) -> bool:
    message = str(exc)
    return (
        message in _RETRYABLE_OUTPUT_MESSAGES
        or message.startswith("replacements count must be between ")
        or message.startswith("replacement target not found in original:")
        or message.startswith("replacement target occurs more than once in original:")
        or message.startswith("generated Python syntax is invalid:")
    )


def materialize_replacements(original: str, replacements: object) -> str:
    if not isinstance(original, str):
        raise ProposalError("original text must be a string")
    if not isinstance(replacements, list):
        raise ProposalError("replacements must be a list")
    if not (1 <= len(replacements) <= MAX_REPLACEMENTS):
        raise ProposalError(f"replacements count must be between 1 and {MAX_REPLACEMENTS}")

    total_bytes = 0
    spans = []
    for item in replacements:
        if not isinstance(item, dict) or set(item.keys()) != {"old", "new"}:
            raise ProposalError("replacement item must be a dict with exactly 'old' and 'new'")
        old = item["old"]
        new = item["new"]
        if not isinstance(old, str) or not old:
            raise ProposalError("replacement old must be a nonempty string")
        if not isinstance(new, str):
            raise ProposalError("replacement new must be a string")
        if old == new:
            raise ProposalError("replacement old and new must differ")

        total_bytes += len(old.encode("utf-8")) + len(new.encode("utf-8"))
        if total_bytes > MAX_REPLACEMENT_BYTES:
            raise ProposalError("aggregate replacement UTF-8 bytes exceed limit")

        start = original.find(old)
        if start == -1:
            raise ProposalError(f"replacement target not found in original: {old!r}")
        if original.find(old, start + 1) != -1:
            raise ProposalError(f"replacement target occurs more than once in original: {old!r}")
        spans.append((start, start + len(old), new))

    spans.sort(key=lambda s: (s[0], s[1]))
    pieces = []
    last_end = 0
    for start, end, new in spans:
        if start < last_end:
            raise ProposalError("replacement spans overlap")
        pieces.append(original[last_end:start])
        pieces.append(new)
        last_end = end
    pieces.append(original[last_end:])
    return "".join(pieces)


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


def bounded_file_bytes(
    root: Path,
    path: Path,
    limit: int,
    label: str,
    failure_callback=None,
) -> bytes:
    """Read one project file without following links or allocating past limit + 1."""
    if limit < 0:
        raise ProposalError("file byte limit cannot be negative")
    root = root.resolve(strict=True)
    candidate = path if path.is_absolute() else root / path
    candidate = Path(os.path.abspath(candidate))
    if not candidate.is_relative_to(root):
        if failure_callback is not None:
            raise failure_callback("TASK_FILE_OUTSIDE_ROOT", None)
        raise ProposalError(f"{label} is outside the project root")
    safe_path: str | None = None
    if candidate != root:
        rel_str = candidate.relative_to(root).as_posix()
        if (
            len(rel_str.encode("utf-8")) <= _MAX_BOUNDARY_PATH_BYTES
            and "\0" not in rel_str
            and "\\" not in rel_str
            and not any(part in {"", ".", ".."} for part in rel_str.split("/"))
            and not SECRET.search(rel_str)
        ):
            safe_path = rel_str
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        if failure_callback is not None:
            if candidate.is_symlink():
                raise failure_callback("TASK_FILE_SYMLINK", safe_path)
            if isinstance(exc, FileNotFoundError):
                raise failure_callback("TASK_FILE_NOT_FOUND", safe_path)
        raise ProposalError(f"cannot safely resolve {label}") from exc
    if resolved != candidate:
        if failure_callback is not None:
            raise failure_callback("TASK_FILE_SYMLINK", safe_path)
        raise ProposalError(f"{label} is a symlink or path escape")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as exc:
        if failure_callback is not None:
            if isinstance(exc, FileNotFoundError):
                raise failure_callback("TASK_FILE_NOT_FOUND", safe_path)
            raise failure_callback("TASK_FILE_UNREADABLE", safe_path)
        raise ProposalError(f"cannot safely open {label}") from exc
    try:
        try:
            before = os.fstat(descriptor)
        except OSError as exc:
            if failure_callback is not None:
                if isinstance(exc, FileNotFoundError):
                    raise failure_callback("TASK_FILE_NOT_FOUND", safe_path)
                raise failure_callback("TASK_FILE_UNREADABLE", safe_path)
            raise
        if not stat.S_ISREG(before.st_mode):
            if failure_callback is not None:
                raise failure_callback("TASK_FILE_NOT_REGULAR", safe_path)
            raise ProposalError(f"{label} is not a regular file")
        # A hardlink can make bytes outside the project reachable through an
        # apparently safe in-project pathname. Proposal inputs must be unique.
        if before.st_nlink != 1:
            if failure_callback is not None:
                raise failure_callback("TASK_FILE_HARDLINKED", safe_path)
            raise ProposalError(f"{label} is hardlinked and cannot be trusted")
        if before.st_size > limit:
            if failure_callback is not None:
                raise failure_callback("TASK_BUDGET_EXCEEDED", safe_path)
            raise ProposalError(f"{label} exceeds the safe context budget")
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                data = source.read(limit + 1)
        except OSError as exc:
            if failure_callback is not None:
                if isinstance(exc, FileNotFoundError):
                    raise failure_callback("TASK_FILE_NOT_FOUND", safe_path)
                raise failure_callback("TASK_FILE_UNREADABLE", safe_path)
            raise
        if len(data) > limit:
            if failure_callback is not None:
                raise failure_callback("TASK_BUDGET_EXCEEDED", safe_path)
            raise ProposalError(f"{label} exceeds the safe context budget")
        try:
            after = os.fstat(descriptor)
        except OSError as exc:
            if failure_callback is not None:
                if isinstance(exc, FileNotFoundError):
                    raise failure_callback("TASK_FILE_NOT_FOUND", safe_path)
                raise failure_callback("TASK_FILE_UNREADABLE", safe_path)
            raise
        try:
            named = os.stat(candidate, follow_symlinks=False)
            final_resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            if failure_callback is not None:
                raise failure_callback("TASK_FILE_CHANGED_DURING_READ", safe_path)
            raise ProposalError(f"{label} changed while it was being read") from exc
        if (file_fingerprint(before) != file_fingerprint(after)
                or file_fingerprint(after) != file_fingerprint(named)
                or final_resolved != candidate):
            if failure_callback is not None:
                raise failure_callback("TASK_FILE_CHANGED_DURING_READ", safe_path)
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
    *,
    generation_completion: GenerationRecoveryPolicy | None = None,
) -> dict | None:
    """Drain all pipes concurrently while enforcing one hard stdout+stderr cap."""
    if max_output < 1 or process.stdin is None or process.stdout is None or process.stderr is None:
        raise ProposalError("invalid bounded process configuration")
    if generation_completion is not None:
        if type(generation_completion) is not GenerationRecoveryPolicy:
            raise ProposalError("invalid generation completion policy")
        return capture_generation_process(
            process, input_bytes, stdout_path, stderr_path, timeout, max_output,
            generation_completion, error_type=ProposalError, terminate=terminate_process,
            stream_chunk_size=STREAM_CHUNK_SIZE,
        )
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
            if input_offset != len(input_bytes):
                raise ProposalError("CLI input was not completely written")
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
            env=dict(os.environ, AGY_CLI_DISABLE_AUTO_UPDATE='true'),
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


def _unique_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ProposalError("duplicate JSON key")
        result[key] = value
    return result


def unique_json(text: str):
    return json.loads(text, object_pairs_hook=_unique_pairs)


def _exact_equal(a: object, b: object) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if a.keys() != b.keys():
            return False
        return all(_exact_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_exact_equal(x, y) for x, y in zip(a, b))
    return a == b


def _deduplicate_response_with_first_document_span(text: str) -> tuple[dict, bytes]:
    decoder = json.JSONDecoder(object_pairs_hook=_unique_pairs)
    idx = 0
    n = len(text)
    while idx < n and text[idx].isspace():
        idx += 1
    docs = []
    first_span = b""
    while idx < n:
        if len(docs) >= 4:
            raise RetryableProviderSessionError(
                "CLI response is not one valid JSON document"
            )
        start = idx
        try:
            doc, end = decoder.raw_decode(text, idx)
        except (json.JSONDecodeError, ProposalError) as exc:
            raise RetryableProviderSessionError(
                "CLI response is not one valid JSON document"
            ) from exc
        if not docs:
            first_span = text[start:end].encode("utf-8")
        docs.append(doc)
        idx = end
        if idx < n:
            if not text[idx].isspace():
                raise RetryableProviderSessionError(
                    "CLI response is not one valid JSON document"
                )
            while idx < n and text[idx].isspace():
                idx += 1

    if len(docs) < 2:
        raise RetryableProviderSessionError(
            "CLI response is not one valid JSON document"
        )

    first = docs[0]
    if not isinstance(first, dict) or set(first.keys()) != CANONICAL_RESPONSE_KEYS:
        raise RetryableProviderSessionError(
            "CLI response is not one valid JSON document"
        )
    first_bytes = json.dumps(
        first, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    for other in docs[1:]:
        if not isinstance(other, dict):
            raise RetryableProviderSessionError(
                "CLI response is not one valid JSON document"
            )
        other_keys = set(other.keys())
        if other_keys == CANONICAL_RESPONSE_KEYS:
            cleaned = other
        elif other_keys == (CANONICAL_RESPONSE_KEYS | DECORATION_KEYS):
            for dec_key in DECORATION_KEYS:
                val = other[dec_key]
                if (
                    not isinstance(val, str)
                    or not val
                    or len(val.encode("utf-8")) > MAX_DECORATION_BYTES
                ):
                    raise RetryableProviderSessionError(
                        "CLI response is not one valid JSON document"
                    )
            cleaned = {k: v for k, v in other.items() if k in CANONICAL_RESPONSE_KEYS}
        else:
            raise RetryableProviderSessionError(
                "CLI response is not one valid JSON document"
            )
        if not _exact_equal(first, cleaned):
            raise RetryableProviderSessionError(
                "CLI response is not one valid JSON document"
            )
        if (
            json.dumps(
                cleaned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            != first_bytes
        ):
            raise RetryableProviderSessionError(
                "CLI response is not one valid JSON document"
            )
    return first, first_span


def _deduplicate_response(text: str) -> dict:
    first, _ = _deduplicate_response_with_first_document_span(text)
    return first


def load_runtime_contract(root: Path) -> dict[str, str]:
    """Load and validate the one machine-readable Gemini execution contract."""
    root = root.resolve(strict=True)
    path = root / GEMINI_RUNTIME_CONTRACT_REL
    contract = unique_json(bounded_file_text(
        root, path, 16 * 1024, "Gemini runtime contract"
    ))
    if not isinstance(contract, dict) or set(contract) != RUNTIME_CONTRACT_KEYS:
        raise ProposalError("Gemini runtime contract has unexpected keys")
    if (contract.get("schema_version") != "1.3" or contract.get("provider") != "antigravity"
            or contract.get("completion_policy") != GENERATION_COMPLETION_POLICY):
        raise ProposalError("Gemini runtime contract has an unsupported schema or provider")
    model = contract.get("model")
    effort = contract.get("effort")
    worker_model = contract.get("worker_model")
    worker_effort = contract.get("worker_effort")
    cli_version = contract.get("cli_version")
    cli_sha256 = contract.get("cli_sha256")
    if (not isinstance(model, str) or not re.fullmatch(r"gemini-[A-Za-z0-9][A-Za-z0-9._-]{1,79}", model)
            or not isinstance(effort, str) or effort not in VALID_EFFORTS
            or not isinstance(worker_model, str)
            or not re.fullmatch(r"gemini-[A-Za-z0-9][A-Za-z0-9._-]{1,79}", worker_model)
            or not isinstance(worker_effort, str) or worker_effort not in VALID_EFFORTS
            or not isinstance(cli_version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", cli_version)
            or not isinstance(cli_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", cli_sha256)):
        raise ProposalError("Gemini runtime contract contains an invalid pin")
    return contract


def validate_runtime_contract(root: Path) -> dict[str, str]:
    """Keep executable pins fixed; role selection belongs to the shared config."""
    contract = load_runtime_contract(root)
    loaded_at_startup = globals().get("RUNTIME_CONTRACT")
    if loaded_at_startup is not None and contract != loaded_at_startup:
        raise ProposalError("Gemini runtime contract changed after runner startup; restart is required")
    configured_gemini_roles(root)
    for relative in GEMINI_RUNTIME_REFERENCE_PATHS:
        if relative == AI_CONFIG_REL:
            continue
        text = bounded_file_text(root, root / relative, MAX_CONTEXT, f"Gemini runtime reference {relative}")
        if AI_CONFIG_REL.name not in text:
            raise ProposalError(f"Gemini model configuration reference drift: {relative}")
    return contract


def _validate_gemini_roles(settings: object) -> dict[str, dict[str, str]]:
    if type(settings) is not dict or set(settings) != {"worker", "reviewer"}:
        raise ProposalError("Gemini model configuration has invalid roles")
    result = {}
    for role in ("worker", "reviewer"):
        value = settings[role]
        if (type(value) is not dict or set(value) != {"provider", "model", "reasoning_effort"}
                or value.get("provider") != "antigravity"
                or type(value.get("model")) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", value["model"])
                or type(value.get("reasoning_effort")) is not str
                or value.get("reasoning_effort") not in VALID_EFFORTS):
            raise ProposalError("Gemini model configuration has invalid role settings")
        result[role] = dict(value)
    return result


def configured_gemini_roles(root: Path) -> dict[str, dict[str, str]]:
    """Read both Gemini roles from one validated public configuration snapshot."""
    try:
        roles = load_ai_config(root)["roles"]
    except AIConfigError as exc:
        raise ProposalError(f"Gemini model configuration: {exc}") from None
    return _validate_gemini_roles({role: roles[role] for role in ("worker", "reviewer")})


def configured_gemini_role(root: Path, role: str) -> dict[str, str]:
    if role not in {"worker", "reviewer", "verifier"}:
        raise ProposalError("invalid Gemini configuration role")
    return configured_gemini_roles(root)["reviewer" if role == "verifier" else role]


def bound_gemini_role(status: dict, role: str) -> dict[str, str] | None:
    """Validate a retained role snapshot without applying today's configuration."""
    if role not in {"worker", "reviewer", "verifier"}:
        raise ProposalError("invalid Gemini configuration role")
    if "model_config" not in status and "model_config_sha256" not in status:
        return None  # Historical proofs retain their original shape and pins.
    settings = _validate_gemini_roles(status.get("model_config"))
    if status.get("model_config_sha256") != sha(serialize_json(settings)):
        raise ProposalError("Gemini model configuration snapshot binding mismatch")
    return settings["reviewer" if role == "verifier" else role]


# Compatibility snapshots for old readers/tests. Fresh invocations resolve the
# shared role configuration in run(); the runtime JSON remains historical pin
# provenance so changing a role does not invalidate retained completion proofs.
RUNTIME_CONTRACT = load_runtime_contract(ROOT)
_STARTUP_GEMINI_ROLES = configured_gemini_roles(ROOT)
MODEL = _STARTUP_GEMINI_ROLES["reviewer"]["model"]
EFFORT = _STARTUP_GEMINI_ROLES["reviewer"]["reasoning_effort"]
WORKER_MODEL = _STARTUP_GEMINI_ROLES["worker"]["model"]
WORKER_EFFORT = _STARTUP_GEMINI_ROLES["worker"]["reasoning_effort"]
EXPECTED_CLI_VERSION = RUNTIME_CONTRACT["cli_version"]
EXPECTED_CLI_SHA256 = RUNTIME_CONTRACT["cli_sha256"]
ALLOWED_SHELL_SCRIPTS = frozenset({
    "scripts/two_site_autopilot_tmux.sh",
    "scripts/two_site_autopilot_monitor.sh",
})
TRUSTED_SHELL_SCRIPTS = ALLOWED_SHELL_SCRIPTS


def is_retryable_zero_token_stream(raw: str, expected_model: str = MODEL) -> bool:
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        events = [unique_json(line) for line in raw.splitlines() if line.strip()]
    except Exception:
        return False
    if not events or any(not isinstance(e, dict) for e in events):
        return False
    for event in events:
        kind = event.get("event")
        if kind in {"init", "result", "step_update"} and not isinstance(event.get(kind), dict):
            return False
    init = [e for e in events if e.get("event") == "init"]
    terminal = [e.get("result") for e in events if e.get("event") == "result"]
    if len(init) != 1 or len(terminal) != 1:
        return False
    init_payload = init[0].get("init")
    if not isinstance(init_payload, dict) or init_payload.get("model") != expected_model:
        return False
    session = init[0].get("conversation_id")
    if not isinstance(session, str) or not session.strip():
        return False
    envelope = terminal[0]
    if not isinstance(envelope, dict):
        return False
    if envelope.get("conversation_id") != session:
        return False
    for event in events:
        if "conversation_id" in event and event.get("conversation_id") != session:
            return False
        if (
            "tool_name" in event
            or "tool_info" in event
            or "subagent_info" in event
            or event.get("step_type") in {"tool", "subagent"}
        ):
            return False
        if event.get("event") == "step_update":
            step = event.get("step_update")
            if isinstance(step, dict) and (
                "tool_name" in step
                or "tool_info" in step
                or "subagent_info" in step
                or step.get("step_type") in {"tool", "subagent"}
            ):
                return False
    if envelope.get("status") != "ERROR":
        return False
    error = envelope.get("error")
    if error != "Agent execution terminated due to error.":
        return False
    if re.search(
        r"auth|quota|rate[\s_-]*limit|time[\s_-]*out|timed[\s_-]*out|deadline|network|connect",
        error,
        re.IGNORECASE,
    ):
        return False
    response = envelope.get("response")
    if response not in ("", None):
        return False
    if envelope.get("structured_output"):
        return False
    denied = envelope.get("denied_actions")
    if denied not in (None, [], ()):
        return False
    usage = envelope.get("usage")
    if not isinstance(usage, dict) or not usage:
        return False
    if not all(type(v) is int and v == 0 for v in usage.values()):
        return False
    return True


def is_retryable_interrupted_stream(raw: str, expected_model: str = MODEL) -> bool:
    """Recognize one safe, retryable provider-stream interruption.

    This predicate deliberately returns only a boolean: provider-provided error
    text is never copied into status, telemetry, or a later prompt. A complete
    candidate is still required; a partial stream is never resumed or applied.
    """
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        events = [unique_json(line) for line in raw.splitlines() if line.strip()]
    except Exception:
        return False
    if not events or any(not isinstance(event, dict) for event in events):
        return False
    for event in events:
        kind = event.get("event")
        if kind in {"init", "result", "step_update"} and not isinstance(event.get(kind), dict):
            return False
    init = [event for event in events if event.get("event") == "init"]
    terminal = [event.get("result") for event in events if event.get("event") == "result"]
    if len(init) != 1 or len(terminal) != 1:
        return False
    init_payload = init[0].get("init")
    if not isinstance(init_payload, dict) or init_payload.get("model") != expected_model:
        return False
    session = init[0].get("conversation_id")
    envelope = terminal[0]
    if (not isinstance(session, str) or not session.strip() or not isinstance(envelope, dict)
            or envelope.get("conversation_id") != session or envelope.get("status") != "ERROR"):
        return False
    for event in events:
        if event.get("conversation_id") not in (None, session):
            return False
        if (
            "tool_name" in event
            or "tool_info" in event
            or "subagent_info" in event
            or event.get("step_type") in {"tool", "subagent"}
        ):
            return False
        if event.get("event") == "step_update":
            step = event.get("step_update")
            if not isinstance(step, dict) or step.get("conversation_id") not in (None, session):
                return False
            if (
                "tool_name" in step
                or "tool_info" in step
                or "subagent_info" in step
                or step.get("step_type") in {"tool", "subagent"}
            ):
                return False
    if envelope.get("denied_actions") not in (None, [], ()):
        return False
    error = envelope.get("error")
    if not isinstance(error, str) or len(error.encode("utf-8")) > 4096:
        return False
    return bool(re.search(
        r"\b(?:stream\s+(?:was\s+)?interrupted|interrupted\s+(?:the\s+)?stream)\b",
        error,
        re.IGNORECASE,
    ))


def relative_file(root: Path, name: str, failure_factory=None) -> Path:
    if (not isinstance(name, str) or not name or "\\" in name or "\0" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        if failure_factory is not None:
            raise failure_factory("TASK_PATH_OUTSIDE_SCOPE", name)
        raise ProposalError("noncanonical file path")
    path = PurePosixPath(name)
    if (path.is_absolute() or any(part in PROTECTED for part in path.parts)
            or any(part.startswith(".env") and not part.endswith(".example") for part in path.parts)
            or (path.suffix.lower() not in {".py", ".md", ".json", ".yaml", ".yml", ".ts", ".vue", ".css", ".example"}
                and name not in ALLOWED_SHELL_SCRIPTS)):
        if failure_factory is not None:
            raise failure_factory("TASK_PATH_OUTSIDE_SCOPE", name)
        raise ProposalError("file outside source/document scope")
    target = root / name
    if target.resolve() != target or not target.is_relative_to(root):
        if failure_factory is not None:
            raise failure_factory("TASK_FILE_SYMLINK", name)
        raise ProposalError("symlink or path escape")
    return target


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["/usr/bin/git", "--no-replace-objects", "-C", str(root), *args],
        text=True, timeout=20, stderr=subprocess.DEVNULL,
    ).strip()


def canonical_cli_source(root: Path) -> Path:
    common_dir = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    return common_dir.parent / ".local/tools" / f"agy-{EXPECTED_CLI_VERSION}-{EXPECTED_CLI_SHA256[:16]}.pin"


@contextlib.contextmanager
def proposal_cli_context(
    root: Path,
    cli: Path | None = None,
) -> Iterator[tuple[Path, Path, dict[str, str]]]:
    if cli is None:
        source = canonical_cli_source(root)
        is_default = True
    else:
        source = Path(cli)
        if not source.is_absolute():
            source = Path(os.path.abspath(Path(root) / source))
        is_default = False

    if not os.path.lexists(source):
        raise FileNotFoundError(f"CLI source not found: {source}")

    lock_descriptor, local = acquire_proposal_lock(root)
    try:
        with prepare_disposable_cli(
            source,
            expected_sha256=EXPECTED_CLI_SHA256 if is_default else None,
            require_read_only=is_default,
        ) as (disposable_path, evidence):
            yield local, disposable_path, evidence
    finally:
        release_proposal_lock(lock_descriptor)


def validate_assignment_scope(root: Path, task: dict) -> None:
    scope_vars = (
        "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED",
        "TWO_SITE_ASSIGNMENT_ID",
        "TWO_SITE_ASSIGNMENT_SHA256",
    )
    present = [var for var in scope_vars if var in os.environ]
    if not present:
        return
    if len(present) != len(scope_vars):
        raise ProposalError("assignment scope validation failed")
    required = os.environ["TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED"]
    assignment_id = os.environ["TWO_SITE_ASSIGNMENT_ID"]
    assignment_sha256 = os.environ["TWO_SITE_ASSIGNMENT_SHA256"]
    if required != "1" or not assignment_id.strip() or not assignment_sha256.strip():
        raise ProposalError("assignment scope validation failed")
    try:
        scope = load_expected_assignment_scope(root, assignment_id, assignment_sha256)
        validate_task_scope(scope, task)
    except AssignmentScopeError as exc:
        reason = str(exc)
        if reason == "Task allowed_file is outside assignment scope":
            code = "TASK_PATH_OUTSIDE_SCOPE"
        elif reason == "task.goal does not contain assignment ID as a bounded token":
            code = "TASK_ASSIGNMENT_TOKEN_MISSING"
        elif reason == "Assignment has expired":
            code = "ASSIGNMENT_EXPIRED"
        else:
            code = "ASSIGNMENT_CONTRACT_INVALID"
        raise ProposalError(f"assignment scope validation failed [{code}]") from exc
    except Exception as exc:
        raise ProposalError("assignment scope validation failed") from exc


class _DuplicateTaskKeyError(Exception):
    pass


def _task_unique_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _DuplicateTaskKeyError()
        result[key] = value
    return result


def _is_tracked_test(root: Path, file_path: str) -> bool:
    target = root / file_path
    if not target.is_file():
        return False
    try:
        resolved = target.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    if resolved != target:
        return False
    try:
        out = git(root, "ls-files", "--", file_path)
        return out.strip() == file_path
    except Exception:
        return False


def validate_test_specification(
    root: Path,
    spec: str,
    allowed_files: list[str] | set[str] | tuple[str, ...],
) -> None:
    if not isinstance(spec, str) or not spec.strip() or spec != spec.strip():
        raise ProposalError(f"invalid test specification: {spec!r}")
    if len(spec.encode("utf-8")) > 512:
        raise ProposalError("test specification exceeds length limit")
    if any(ch.isspace() for ch in spec):
        raise ProposalError(f"test specification contains whitespace or options: {spec[:120]!r}")
    if "\\" in spec or "\0" in spec:
        raise ProposalError(f"test specification contains invalid characters: {spec[:120]!r}")

    parts = spec.split("::")
    file_path = parts[0]
    node_parts = parts[1:]

    if len(parts) > 1:
        if any(not part or not part.isidentifier() for part in node_parts):
            raise ProposalError(f"invalid test specification node parts: {spec[:120]!r}")

    if file_path.startswith("/") or file_path.startswith("./") or file_path.endswith("/"):
        raise ProposalError(f"noncanonical test specification path: {spec[:120]!r}")

    segments = file_path.split("/")
    if any(not seg or seg in {"", ".", ".."} for seg in segments):
        raise ProposalError(f"noncanonical test specification path: {spec[:120]!r}")
    if segments[0] != "tests":
        raise ProposalError(f"test specification must be under tests/: {spec[:120]!r}")
    if not file_path.endswith(".py"):
        raise ProposalError(f"test specification must end with .py: {spec[:120]!r}")

    filename = segments[-1]
    if not filename.startswith("test_"):
        raise ProposalError(f"test file name must start with test_: {spec[:120]!r}")
    stem = filename[:-3]
    if not stem.isidentifier():
        raise ProposalError(f"test file name must be a valid Python identifier: {spec[:120]!r}")

    for seg in segments[1:-1]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", seg):
            raise ProposalError(f"invalid directory in test specification: {spec[:120]!r}")

    allowed_set = set(allowed_files)
    if file_path in allowed_set:
        return
    if not _is_tracked_test(root, file_path):
        raise ProposalError(
            f"test specification does not resolve to tracked test or allowed overlay: {spec[:120]!r}"
        )


def validate_task_test_selectors(root: Path, task: dict) -> None:
    goal = task.get("goal", "")
    if not isinstance(goal, str) or "\nCANONICAL_SOURCE_EXCERPTS_JSON:\n" not in goal:
        return
    selectors = task.get("test_selectors", [])
    if not isinstance(selectors, list):
        raise ProposalError("task test_selectors must be a list")
    allowed_files = task.get("allowed_files", [])
    if not isinstance(allowed_files, list):
        allowed_files = []
    for spec in selectors:
        validate_test_specification(root, spec, allowed_files)


def _load_task_snapshot(
    root: Path,
    task_path: Path,
    *,
    assignment_facts: list[dict[str, str | None]] | None = None,
) -> tuple[dict, dict]:
    if assignment_facts is not None and (
        not isinstance(assignment_facts, list)
        or type(assignment_facts) is not list
        or len(assignment_facts) != 0
    ):
        raise ProposalError("assignment_facts must be None or an empty list")

    def _task_path_error(code: str, path_name: str | None = None) -> _TaskBoundaryError:
        safe_path = None
        if (
            isinstance(path_name, str)
            and type(path_name) is str
            and len(path_name.encode("utf-8")) <= _MAX_BOUNDARY_PATH_BYTES
            and "\0" not in path_name
            and "\\" not in path_name
            and not path_name.startswith("/")
            and not PurePosixPath(path_name).is_absolute()
            and not (len(path_name) >= 2 and path_name[1] == ":" and path_name[0].isalpha())
            and not any(part in {"", ".", ".."} for part in path_name.split("/"))
            and not SECRET.search(path_name)
        ):
            safe_path = path_name
        return _TaskBoundaryError(code, safe_path)

    raw = bounded_file_bytes(
        root, task_path, MAX_CONTEXT, "task", failure_callback=_task_path_error
    )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _TaskBoundaryError("TASK_SCHEMA_INVALID") from exc
    try:
        task = json.loads(text, object_pairs_hook=_task_unique_pairs)
    except json.JSONDecodeError as exc:
        raise _TaskBoundaryError("TASK_SCHEMA_INVALID") from exc
    except _DuplicateTaskKeyError as exc:
        raise _TaskBoundaryError("TASK_SCHEMA_INVALID") from exc
    required = {"task_id", "goal", "context_files", "allowed_files", "requirements", "test_selectors"}
    if not isinstance(task, dict) or set(task) != required:
        raise _TaskBoundaryError("TASK_SCHEMA_INVALID")
    if (not isinstance(task["task_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", task["task_id"])):
        raise _TaskBoundaryError("TASK_SCHEMA_INVALID")
    if not isinstance(task["goal"], str) or not task["goal"].strip():
        raise _TaskBoundaryError("TASK_SCHEMA_INVALID")
    for key in ("context_files", "allowed_files", "requirements", "test_selectors"):
        values = task[key]
        if (not isinstance(values, list) or not values or len(values) > 32
                or any(not isinstance(x, str) or not x.strip() for x in values)
                or len(set(values)) != len(values)):
            raise _TaskBoundaryError("TASK_SCHEMA_INVALID")
    if SECRET.search(json.dumps(task, ensure_ascii=False)):
        raise _TaskBoundaryError("TASK_CREDENTIAL_DETECTED")
    if assignment_facts is None:
        validate_assignment_scope(root, task)
    else:
        try:
            validate_assignment_scope(root, task)
        except ProposalError as exc:
            cause = exc.__cause__
            if (
                isinstance(cause, AssignmentScopeError)
                and isinstance(getattr(cause, "fact", None), AssignmentFact)
                and getattr(cause, "fact").code == "TASK_ASSIGNMENT_TOKEN_MISSING"
            ):
                assignment_facts.append({"code": "TASK_ASSIGNMENT_TOKEN_MISSING", "path": None})
            else:
                raise
    if "\nCANONICAL_SOURCE_EXCERPTS_JSON:\n" in task["goal"]:
        try:
            from scripts.two_site_cycle_tools import validate_excerpts
        except ModuleNotFoundError:
            from two_site_cycle_tools import validate_excerpts
        validate_excerpts(root, task)
        validate_task_test_selectors(root, task)
    sources = {}
    for name in dict.fromkeys(task["context_files"] + task["allowed_files"]):
        target = relative_file(root, name, failure_factory=_task_path_error)
        if not target.exists():
            if name in task["context_files"]:
                raise _task_path_error("TASK_FILE_NOT_FOUND", name)
            sources[name] = {"sha256": None, "content": None}
            continue
        content = bounded_file_bytes(
            root, target, MAX_CONTEXT, "context file " + name, failure_callback=_task_path_error
        )
        if SECRET.search(content.decode()):
            raise ProposalError("credential-like content excluded from model input")
        sources[name] = {"sha256": sha(content), "content": content.decode()}
    if len(json.dumps({"task": task, "sources": sources}).encode()) > MAX_CONTEXT:
        raise _task_path_error("TASK_BUDGET_EXCEEDED")
    return task, sources


def load_task(root: Path, task_path: Path, *, maintenance_authorization=None,
              evidence_only: bool = False) -> tuple[dict, dict]:
    if maintenance_authorization is not None:
        from scripts.two_site_qoder_maintenance import load_maintenance_task
        return load_maintenance_task(root, task_path, maintenance_authorization,
                                     evidence_only=evidence_only)
    return _load_task_snapshot(root, task_path)


def validate_task_readiness(
    task_or_notes: dict,
    sources: dict | None = None,
    notes: dict | None = None,
) -> dict:
    """Deterministic readiness validator for dependency context."""
    if isinstance(sources, dict):
        if notes is None:
            notes = task_preparation_notes(task_or_notes, sources)
    else:
        notes = task_or_notes
    dep_ctx = notes.get("dependency_context") if isinstance(notes, dict) else {}
    if isinstance(dep_ctx, dict) and dep_ctx.get("persistence_contract_requested"):
        omitted = dep_ctx.get("omitted_model_path_count", 0)
        if type(omitted) is not int or omitted < 0:
            raise ProposalError("invalid omitted model dependency count")
        if omitted:
            raise ProposalError("too many model dependencies: split the task")
        missing = [p for p in dep_ctx.get("missing_full_model_paths", []) if isinstance(p, str)]
        if missing:
            bounded_missing = sorted(dict.fromkeys(missing))[:16]
            raise ProposalError(f"missing model context: {', '.join(bounded_missing)}")
    session_ctx = dep_ctx.get("session_context") if isinstance(dep_ctx, dict) else None
    if isinstance(session_ctx, dict) and session_ctx.get("requested"):
        if not session_ctx.get("adapter_symbols"):
            raise ProposalError(
                "missing live session adapter context: first supply or create a reusable adapter"
            )
        if not session_ctx.get("root_symbols"):
            raise ProposalError("missing explicit live session entrypoint context")
        omitted_required = session_ctx.get("omitted_required_symbol_count", 0)
        omitted_missing = session_ctx.get("omitted_missing_symbol_count", 0)
        omitted_queries = session_ctx.get("omitted_query_operation_count", 0)
        omitted_unresolved = session_ctx.get("omitted_unresolved_symbol_count", 0)
        if any(type(value) is not int or value < 0 for value in (
            omitted_required, omitted_missing, omitted_queries, omitted_unresolved
        )):
            raise ProposalError("invalid live session dependency counts")
        if omitted_required or omitted_missing or omitted_queries or omitted_unresolved:
            raise ProposalError("live session dependency closure exceeds bounded context: split the task")
        unresolved = [
            value for value in session_ctx.get("unresolved_symbols", [])
            if isinstance(value, str)
        ]
        missing = [
            value for value in session_ctx.get("missing_symbols", [])
            if isinstance(value, str)
        ]
        incomplete = sorted(dict.fromkeys(unresolved + missing))
        if incomplete:
            raise ProposalError("missing live session context: " + ", ".join(incomplete[:32]))
        if not session_ctx.get("query_operations"):
            raise ProposalError("live session path has no proven query/result contract")
    return notes


def derive_session_gate(notes: dict) -> dict[str, object]:
    if not isinstance(notes, dict):
        raise ProposalError("invalid preparation_notes for session gate derivation")
    dep_ctx = notes.get("dependency_context")
    if not isinstance(dep_ctx, dict):
        raise ProposalError("missing or invalid dependency_context for session gate derivation")
    session_ctx = dep_ctx.get("session_context")
    if session_ctx is None:
        return {
            "state": "NOT_REQUESTED",
            "activation_reason": "NOT_REQUESTED",
            "root_count": 0,
            "required_symbol_count": 0,
            "query_operation_count": 0,
        }
    if not isinstance(session_ctx, dict):
        raise ProposalError("invalid session_context in dependency_context")
    requested = session_ctx.get("requested")
    if requested is False:
        reason = session_ctx.get("activation_reason")
        if reason != "NOT_REQUESTED":
            raise ProposalError("invalid activation_reason for unrequested session context")
        roots = session_ctx.get("root_symbols", [])
        if not isinstance(roots, list) or len(roots) != 0:
            raise ProposalError("non-empty root_symbols for unrequested session context")
        req_count = session_ctx.get("required_symbol_count", 0)
        if type(req_count) is not int or isinstance(req_count, bool) or req_count != 0:
            raise ProposalError("non-zero required_symbol_count for unrequested session context")
        queries = session_ctx.get("query_operations", [])
        if not isinstance(queries, list) or len(queries) != 0:
            raise ProposalError("non-empty query_operations for unrequested session context")
        return {
            "state": "NOT_REQUESTED",
            "activation_reason": "NOT_REQUESTED",
            "root_count": 0,
            "required_symbol_count": 0,
            "query_operation_count": 0,
        }
    if requested is True:
        reason = session_ctx.get("activation_reason")
        if reason not in ("EXPLICIT_DECLARATION", "LEGACY_HEURISTIC"):
            raise ProposalError(f"invalid session context activation reason: {reason}")
        roots = session_ctx.get("root_symbols")
        if not isinstance(roots, list) or any(not isinstance(r, str) or not r.strip() for r in roots):
            raise ProposalError("invalid root_symbols in session_context")
        root_count = len(roots)
        if root_count > 8:
            raise ProposalError("root_count exceeds bounded maximum")
        req_count = session_ctx.get("required_symbol_count")
        if type(req_count) is not int or isinstance(req_count, bool) or not (0 <= req_count <= MAX_SESSION_FLOW_SYMBOLS):
            raise ProposalError("invalid required_symbol_count in session_context")
        queries = session_ctx.get("query_operations")
        if not isinstance(queries, list):
            raise ProposalError("invalid query_operations in session_context")
        query_count = len(queries)
        if query_count > MAX_SESSION_QUERY_HINTS:
            raise ProposalError("query_operation_count exceeds bounded maximum")
        if (
            session_ctx.get("omitted_required_symbol_count", 0) != 0
            or session_ctx.get("omitted_query_operation_count", 0) != 0
            or session_ctx.get("omitted_missing_symbol_count", 0) != 0
            or session_ctx.get("omitted_unresolved_symbol_count", 0) != 0
            or session_ctx.get("missing_symbols")
            or session_ctx.get("unresolved_symbols")
        ):
            raise ProposalError("session_context contains unpassed or omitted symbols")
        return {
            "state": "REQUESTED_AND_PASSED",
            "activation_reason": reason,
            "root_count": root_count,
            "required_symbol_count": req_count,
            "query_operation_count": query_count,
        }
    raise ProposalError("invalid requested flag in session_context")


def _task_contract_text(task: dict) -> str:
    goal = task.get("goal", "")
    if isinstance(goal, str):
        goal = goal.split("CANONICAL_SOURCE_EXCERPTS_JSON:", 1)[0]
    else:
        goal = ""
    requirements = task.get("requirements", [])
    if not isinstance(requirements, list):
        requirements = []
    return "\n".join([goal] + [value for value in requirements if isinstance(value, str)])


def _repository_model_class_paths(root: Path) -> dict[str, set[str]]:
    """Index bounded top-level model declarations without adding them to model input."""
    model_root = root / "models"
    if not model_root.is_dir():
        return {}
    candidates = sorted(path for path in model_root.iterdir() if path.name.endswith(".py"))
    if len(candidates) > 256:
        raise ProposalError("model dependency index exceeds file limit")
    index: dict[str, set[str]] = {}
    for path in candidates:
        relative = path.relative_to(root).as_posix()
        raw = bounded_file_bytes(root, path, MAX_CONTEXT, "model dependency " + relative)
        try:
            tree = ast.parse(raw.decode("utf-8"), filename=relative)
        except (SyntaxError, UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise ProposalError("cannot index authoritative model dependency: " + relative) from exc
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                index.setdefault(node.name, set()).add(relative)
    return index


def _canonical_excerpt_blocks(task: dict) -> list[tuple[str, tuple[str, ...], str]]:
    """Return bounded canonical excerpt text with its declared symbols."""
    goal = task.get("goal", "")
    marker = "CANONICAL_SOURCE_EXCERPTS_JSON:\n"
    if not isinstance(goal, str) or marker not in goal:
        return []
    try:
        payload, _ = json.JSONDecoder().raw_decode(goal.split(marker, 1)[1].lstrip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    excerpts = payload.get("excerpts", []) if isinstance(payload, dict) else []
    blocks: list[tuple[str, tuple[str, ...], str]] = []
    for excerpt in excerpts[:32] if isinstance(excerpts, list) else []:
        if not isinstance(excerpt, dict):
            continue
        path = excerpt.get("path")
        symbols = excerpt.get("symbols", [])
        ranges = excerpt.get("ranges", [])
        if not isinstance(path, str) or not path.endswith(".py"):
            continue
        declared = tuple(value for value in symbols if isinstance(value, str))
        for item in ranges[:64] if isinstance(ranges, list) else []:
            text = item.get("text") if isinstance(item, dict) else None
            if isinstance(text, str):
                blocks.append((path, declared, text))
    return blocks


def _parse_python_fragment(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        lines = text.splitlines()
        indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
        minimum = min(indents) if indents else 0
        try:
            return ast.parse("\n".join(line[minimum:] for line in lines))
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return None


def _supplied_python_facts(task: dict, sources: dict) -> tuple[set[str], list[tuple[str, ast.Module]]]:
    """Return supplied path:symbol identities and parseable source fragments."""
    supplied: set[str] = set()
    trees: list[tuple[str, ast.Module]] = []
    for path, item in sorted(sources.items()):
        content = item.get("content") if isinstance(item, dict) else None
        if not isinstance(path, str) or not path.endswith(".py") or not isinstance(content, str):
            continue
        tree = _parse_python_fragment(content)
        if tree is None:
            continue
        trees.append((path, tree))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                supplied.add(f"{path}:{node.name}")
    for path, declared, text in _canonical_excerpt_blocks(task):
        supplied.update(f"{path}:{symbol}" for symbol in declared)
        tree = _parse_python_fragment(text)
        if tree is not None:
            trees.append((path, tree))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    supplied.add(f"{path}:{node.name}")
    return supplied, trees


def _class_has_session_execute(node: ast.ClassDef) -> bool:
    return any(
        isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
        and member.name == "execute"
        for member in node.body
    )


def _walk_import_scope(tree: ast.AST) -> Iterator[ast.AST]:
    """Walk imports in one module/function scope without crossing nested definitions."""
    stack = list(reversed(getattr(tree, "body", [])))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _project_imports(tree: ast.AST, current_module: str) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    symbols: dict[str, tuple[str, str]] = {}
    modules: dict[str, str] = {}
    package = current_module.split(".")[:-1]
    for node in _walk_import_scope(tree):
        if isinstance(node, ast.ImportFrom):
            prefix = package[:max(0, len(package) - max(0, node.level - 1))] if node.level else []
            module_parts = prefix + (node.module.split(".") if node.module else [])
            module = ".".join(module_parts)
            if not module or module.split(".", 1)[0] not in PROJECT_MODULE_ROOTS:
                continue
            for alias in node.names:
                if alias.name != "*":
                    symbols[alias.asname or alias.name] = (module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in PROJECT_MODULE_ROOTS:
                    modules[alias.asname or alias.name.split(".", 1)[0]] = alias.name
    return symbols, modules


def _module_path(module: str) -> str:
    return module.replace(".", "/") + ".py"


def _module_for_path(path: str) -> str:
    return path[:-3].replace("/", ".") if path.endswith(".py") else path.replace("/", ".")


def _load_project_module(root: Path, module: str, cache: dict) -> dict | None:
    if module in cache:
        return cache[module]
    path = _module_path(module)
    target = root / path
    if not target.is_file():
        init_path = module.replace(".", "/") + "/__init__.py"
        target = root / init_path
        path = init_path
        if not target.is_file():
            cache[module] = None
            return None
    raw = bounded_file_bytes(root, target, MAX_SESSION_SOURCE_BYTES, "session dependency " + path)
    try:
        tree = ast.parse(raw.decode("utf-8"), filename=path)
    except (SyntaxError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProposalError("cannot index live session dependency: " + path) from exc
    definitions = {
        node.name: node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports, imported_modules = _project_imports(tree, module)
    result = {
        "module": module,
        "path": path,
        "tree": tree,
        "definitions": definitions,
        "imports": imports,
        "imported_modules": imported_modules,
    }
    cache[module] = result
    return result


def _resolve_project_symbol(root: Path, module: str, symbol: str, cache: dict) -> tuple[dict, ast.AST] | None:
    """Resolve a function, following bounded project re-exports."""
    seen: set[tuple[str, str]] = set()
    for _ in range(8):
        identity = (module, symbol)
        if identity in seen:
            return None
        seen.add(identity)
        data = _load_project_module(root, module, cache)
        if data is None:
            return None
        definition = data["definitions"].get(symbol)
        if definition is not None:
            return data, definition
        imported = data["imports"].get(symbol)
        if imported is None:
            return None
        module, symbol = imported
    return None


def _walk_function_body(function: ast.AST) -> Iterator[ast.AST]:
    """Walk one function while excluding nested function/class bodies."""
    stack = list(reversed(getattr(function, "body", [])))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _call_leaf_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _explicit_mock_expression(node: ast.AST) -> bool:
    if isinstance(node, ast.Call):
        return _call_leaf_name(node.func) in {"AsyncMock", "MagicMock", "Mock"}
    return isinstance(node, ast.Name) and node.id in {"AsyncMock", "MagicMock", "Mock"}


def _explicit_patch_targets(trees: list[tuple[str, ast.Module]]) -> set[str]:
    targets: set[str] = set()
    for _, tree in trees:
        mock_names = {
            target.id
            for assignment in ast.walk(tree)
            if isinstance(assignment, (ast.Assign, ast.AnnAssign))
            for target in (
                assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            )
            if isinstance(target, ast.Name)
            and isinstance(assignment.value, ast.Call)
            and _call_leaf_name(assignment.value.func) in {"AsyncMock", "MagicMock", "Mock"}
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_leaf_name(node.func) != "patch" or not node.args:
                continue
            target = node.args[0]
            if not isinstance(target, ast.Constant) or not isinstance(target.value, str):
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            explicit = "return_value" in keywords or "new_callable" in keywords
            new_value = keywords.get("new", ast.Constant(None))
            explicit = explicit or _explicit_mock_expression(new_value)
            explicit = explicit or (isinstance(new_value, ast.Name) and new_value.id in mock_names)
            explicit = explicit or (len(node.args) > 1 and _explicit_mock_expression(node.args[1]))
            if explicit:
                targets.add(target.value)
    return targets


def _session_parameter_names(function: ast.AST) -> set[str]:
    arguments = getattr(function, "args", None)
    if arguments is None:
        return set()
    names = [arg.arg for arg in arguments.posonlyargs + arguments.args + arguments.kwonlyargs]
    return {
        name for name in names
        if name == "db" or name.endswith("_db") or "session" in name.lower()
    }


def _handler_session_names(function: ast.AST) -> set[str]:
    names = set(_session_parameter_names(function))
    for node in _walk_function_body(function):
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                if (
                    isinstance(item.context_expr, ast.Call)
                    and _call_leaf_name(item.context_expr.func) == "AsyncSessionLocal"
                ):
                    if isinstance(item.optional_vars, ast.Name):
                        names.add(item.optional_vars.id)
                    elif item.optional_vars is not None:
                        for target in ast.walk(item.optional_vars):
                            if isinstance(target, ast.Name):
                                names.add(target.id)
    return names


def _call_passes_session(call: ast.Call, session_names: set[str]) -> bool:
    values = list(call.args) + [kw.value for kw in call.keywords]
    return any(isinstance(value, ast.Name) and value.id in session_names for value in values)


def _resolve_called_symbol(call: ast.Call, data: dict) -> tuple[str, str] | None:
    func = call.func
    if isinstance(func, ast.Name):
        if func.id in data["definitions"]:
            return data["module"], func.id
        return data["imports"].get(func.id)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module = data["imported_modules"].get(func.value.id)
        if module:
            return module, func.attr
    return None


def _result_apis_for_call(function: ast.AST, execute_call: ast.Call) -> str:
    nodes = list(_walk_function_body(function))
    parents = {
        child: parent for parent in nodes
        for child in ast.iter_child_nodes(parent)
    }
    apis: list[str] = []
    cursor: ast.AST = execute_call
    for _ in range(8):
        cursor = parents.get(cursor)
        if cursor is None:
            break
        if isinstance(cursor, ast.Attribute) and cursor.attr in SESSION_RESULT_APIS:
            apis.append(cursor.attr)
    assigned_names: set[str] = set()
    cursor = execute_call
    for _ in range(8):
        cursor = parents.get(cursor)
        if cursor is None:
            break
        if isinstance(cursor, (ast.Assign, ast.AnnAssign)):
            targets = cursor.targets if isinstance(cursor, ast.Assign) else [cursor.target]
            assigned_names.update(target.id for target in targets if isinstance(target, ast.Name))
            break
        if isinstance(cursor, ast.stmt):
            break
    longest_assigned_chain: list[str] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        chain: list[str] = []
        chain_cursor: ast.AST = node
        while isinstance(chain_cursor, (ast.Call, ast.Attribute, ast.Await)):
            if isinstance(chain_cursor, ast.Call):
                chain_cursor = chain_cursor.func
            elif isinstance(chain_cursor, ast.Await):
                chain_cursor = chain_cursor.value
            else:
                chain.append(chain_cursor.attr)
                chain_cursor = chain_cursor.value
        if isinstance(chain_cursor, ast.Name) and chain_cursor.id in assigned_names:
            result_chain = [name for name in reversed(chain) if name in SESSION_RESULT_APIS]
            if len(result_chain) > len(longest_assigned_chain):
                longest_assigned_chain = result_chain
    apis.extend(longest_assigned_chain)
    return ".".join(dict.fromkeys(apis)) or "direct"


def _statement_text(function: ast.AST, call: ast.Call) -> str:
    if not call.args:
        return ""
    expression = call.args[0]
    if isinstance(expression, ast.Name):
        for node in _walk_function_body(function):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if (node.value is not None and any(
                    isinstance(target, ast.Name) and target.id == expression.id for target in targets
                )):
                    expression = node.value
                    break
    try:
        return " ".join(ast.unparse(expression).split())[:160]
    except (ValueError, RecursionError):
        return ""


def _parse_explicit_session_declarations(contract_text: str) -> list[tuple[str, str]]:
    declarations: list[tuple[str, str]] = []
    in_fence = False
    for line in contract_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in re.finditer(r"(?<![A-Za-z0-9_])session_context\s*:", line):
            start = match.start()
            prefix = line[:start]
            if prefix.count("`") % 2 == 1 or prefix.count('"') % 2 == 1:
                continue
            if prefix.count("'") % 2 == 1 and "'" in line[match.end():]:
                continue
            if prefix and prefix[-1] in ("`", '"', "'"):
                continue

            rest = line[match.end():]
            body = rest.split(";", 1)[0] if ";" in rest else rest
            body = body.strip()
            if not body:
                raise ProposalError("malformed session_context declaration: missing body")
            exact_prefix = "exact entrypoint"
            if body.startswith(exact_prefix):
                remainder = body[len(exact_prefix):]
                if not remainder or not remainder[0].isspace():
                    raise ProposalError(f"malformed session_context declaration: {body}")
                entrypoint_str = remainder.strip()
            else:
                entrypoint_str = body
            if not entrypoint_str or len(entrypoint_str.split()) != 1:
                raise ProposalError(f"malformed session_context declaration: {body}")
            entrypoint = entrypoint_str.split()[0]
            if ":" in entrypoint:
                parts = entrypoint.split(":")
                if len(parts) != 2:
                    raise ProposalError(f"malformed session_context entrypoint: {entrypoint}")
                path_part, symbol = parts
                if not symbol.isidentifier():
                    raise ProposalError(f"malformed session_context entrypoint symbol: {entrypoint}")
                if "\\" in path_part or path_part.startswith("/") or not path_part.endswith(".py"):
                    raise ProposalError(f"noncanonical session_context entrypoint path: {entrypoint}")
                segments = path_part[:-3].split("/")
                if any(not seg.isidentifier() for seg in segments):
                    raise ProposalError(f"noncanonical session_context entrypoint path: {entrypoint}")
                if segments[0] not in PROJECT_MODULE_ROOTS:
                    raise ProposalError(f"out-of-project session_context entrypoint: {entrypoint}")
                module = ".".join(segments)
                declarations.append((module, symbol))
            else:
                parts = entrypoint.split(".")
                if len(parts) < 3 or any(not part.isidentifier() for part in parts):
                    raise ProposalError(f"malformed session_context entrypoint: {entrypoint}")
                if parts[0] not in PROJECT_MODULE_ROOTS:
                    raise ProposalError(f"out-of-project session_context entrypoint: {entrypoint}")
                module = ".".join(parts[:-1])
                symbol = parts[-1]
                declarations.append((module, symbol))
    return declarations


def _live_session_roots(contract_text: str, trees: list[tuple[str, ast.Module]]) -> list[tuple[str, str]]:
    roots = {(module, symbol) for module, symbol in QUALIFIED_PROJECT_SYMBOL_RE.findall(contract_text)}
    if roots:
        return sorted(roots)
    for path, tree in trees:
        current_module = _module_for_path(path)
        imports, _ = _project_imports(tree, current_module)
        for visible_name, target in imports.items():
            if (visible_name[:1].islower() or visible_name.startswith("_")) and re.search(
                rf"\b{re.escape(visible_name)}\b", contract_text
            ):
                roots.add(target)
    return sorted(roots)


def _session_text_requests_adapter(contract_text: str) -> bool:
    """Ignore only a closed grammar of explicit negative Session clauses/lists.

    This is a bounded lexical check, not a general negation parser. Explicit
    session_context declarations and source-detected adapters retain precedence.
    """
    boundary = r"[.;:\n!?]"
    action = r"\b(?:use|using|reuse|reusing|add|build|create|implement|introduce|provide|include|construct|instantiate|supply|copy|call|invoke|execute|exercise)\b"
    for match in SESSION_TEXT_RE.finditer(contract_text):
        if re.search(boundary, match.group()):
            return True  # Do not let a prohibition cross into another clause.
        prefix = re.split(boundary, contract_text[max(0, match.start() - 160):match.start()])[-1]
        negations = list(re.finditer(r"\b(?:do\s+not|don't|no|without|not)\b", prefix, re.IGNORECASE))
        if len(negations) != 1:
            return True  # Missing or multiple/possibly double negation fails closed.
        tail = prefix[negations[0].end():].strip()
        if negations[0].group().lower() != "no":
            tail = re.sub("^" + action, "", tail, count=1, flags=re.IGNORECASE).strip()
        list_tail = SESSION_TEXT_RE.sub("SESSION_ITEM", tail)
        if not (tail.lower() == "copied handler logic, a" or re.fullmatch(
                r"(?:SESSION_ITEM\s*(?:,\s*|\s+(?:and|or)\b\s*))*(?:a|an|any|the)?",
                list_tail, re.IGNORECASE)):
            return True
        following = contract_text[match.end():match.end() + 160]
        clause_end = re.search(boundary, following)
        if clause_end is None and match.end() + len(following) < len(contract_text):
            return True  # A clipped window cannot prove a complete negative clause.
        suffix = re.split(boundary, following, maxsplit=1)[0].strip()
        # Only a closed negative list may follow; every unknown suffix remains
        # positive/ambiguous (including renewed actions or permission language).
        list_suffix = SESSION_TEXT_RE.sub("SESSION_ITEM", suffix)
        if not (not suffix or suffix.lower() == "or another characterization testcase fixture"
                or re.fullmatch(
                    r"(?:\s*(?:,|and|or)\s*(?:(?:a|an|any|the)\s+)?SESSION_ITEM)+\s*",
                    list_suffix, re.IGNORECASE)):
            return True
        if clause_end is not None and clause_end.group() == "?":
            return True
    return False


def _build_live_session_context(root: Path, task: dict, sources: dict) -> dict:
    supplied, trees = _supplied_python_facts(task, sources)
    contract_text = _task_contract_text(task)
    test_only = bool(task.get("allowed_files")) and all(
        isinstance(path, str) and (path.startswith("tests/") or "/test" in path)
        for path in task.get("allowed_files", [])
    )
    adapters = sorted({
        f"{path}:{node.name}" for path, tree in trees for node in tree.body
        if isinstance(node, ast.ClassDef) and _class_has_session_execute(node)
    })
    explicit_roots: list[tuple[str, str]] = []
    if test_only:
        declared = _parse_explicit_session_declarations(contract_text)
        if declared:
            unique_declared = sorted(set(declared))
            if len(unique_declared) > 1:
                root_strs = [f"{_module_path(m)}:{s}" for m, s in unique_declared]
                raise ProposalError(f"ambiguous session context entrypoints: {', '.join(root_strs)}")
            explicit_roots = unique_declared

    if explicit_roots:
        requested = True
        activation_reason = "EXPLICIT_DECLARATION"
        roots = explicit_roots
    elif (
        test_only
        and LIVE_PATH_RE.search(contract_text)
        and (_session_text_requests_adapter(contract_text) or adapters)
    ):
        requested = True
        activation_reason = "LEGACY_HEURISTIC"
        roots = _live_session_roots(contract_text, trees)
    else:
        requested = False
        activation_reason = "NOT_REQUESTED"
        roots = []

    context = {
        "requested": requested,
        "activation_reason": activation_reason,
        "adapter_symbols": adapters[:8],
        "root_symbols": [],
        "required_symbol_count": 0,
        "missing_symbols": [],
        "omitted_missing_symbol_count": 0,
        "query_operations": [],
        "omitted_query_operation_count": 0,
        "unresolved_symbols": [],
        "omitted_unresolved_symbol_count": 0,
        "omitted_required_symbol_count": 0,
    }
    if not requested:
        return context

    context["root_symbols"] = [f"{_module_path(module)}:{symbol}" for module, symbol in roots[:8]]
    patched = _explicit_patch_targets(trees)
    cache: dict = {}
    queue = [(module, symbol, 0) for module, symbol in roots[:8]]
    enqueued = {f"{module}.{symbol}" for module, symbol in roots[:8]}
    visited: set[str] = set()
    unresolved: set[str] = set()
    queries: list[dict] = []
    omitted_required = max(0, len(roots) - 8)
    while queue:
        module, symbol, depth = queue.pop(0)
        qualified = f"{module}.{symbol}"
        if qualified in patched:
            continue
        resolved = _resolve_project_symbol(root, module, symbol, cache)
        if resolved is None:
            unresolved.add(f"{_module_path(module)}:{symbol}")
            continue
        data, function = resolved
        identity = f"{data['path']}:{function.name}"
        if identity in visited:
            continue
        if len(visited) >= MAX_SESSION_FLOW_SYMBOLS:
            omitted_required += 1
            continue
        visited.add(identity)
        session_names = _handler_session_names(function)
        if not session_names:
            continue
        local_imports, local_modules = _project_imports(function, data["module"])
        resolution_data = dict(data)
        resolution_data["imports"] = {**data["imports"], **local_imports}
        resolution_data["imported_modules"] = {
            **data["imported_modules"], **local_modules,
        }
        for node in _walk_function_body(function):
            if not isinstance(node, ast.Call):
                continue
            if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in session_names
                    and node.func.attr in SESSION_OPERATIONS):
                if len(queries) < MAX_SESSION_QUERY_HINTS:
                    queries.append({
                        "path": data["path"],
                        "symbol": function.name,
                        "line": getattr(node, "lineno", 0),
                        "operation": node.func.attr,
                        "result_api": _result_apis_for_call(function, node),
                        "statement": _statement_text(function, node),
                    })
                else:
                    context["omitted_query_operation_count"] += 1
                continue
            if depth >= 8 or not _call_passes_session(node, session_names):
                continue
            called = _resolve_called_symbol(node, resolution_data)
            if called is None:
                leaf = _call_leaf_name(node.func)
                if leaf and leaf not in SESSION_CALL_SAFE_BUILTINS:
                    unresolved.add(f"{data['path']}:{function.name}->{leaf}")
                continue
            called_name = f"{called[0]}.{called[1]}"
            if called_name in patched or called_name in enqueued:
                continue
            if len(enqueued) >= MAX_SESSION_FLOW_SYMBOLS:
                omitted_required += 1
                continue
            enqueued.add(called_name)
            queue.append((called[0], called[1], depth + 1))

    missing = sorted(visited - supplied)
    context["required_symbol_count"] = len(visited) + omitted_required
    context["missing_symbols"] = missing[:MAX_SESSION_FLOW_MISSING]
    context["omitted_missing_symbol_count"] = max(0, len(missing) - MAX_SESSION_FLOW_MISSING)
    context["query_operations"] = queries
    ordered_unresolved = sorted(unresolved)
    context["unresolved_symbols"] = ordered_unresolved[:MAX_SESSION_FLOW_MISSING]
    context["omitted_unresolved_symbol_count"] = max(
        0, len(ordered_unresolved) - MAX_SESSION_FLOW_MISSING
    )
    context["omitted_required_symbol_count"] = omitted_required
    return context


def enrich_repository_dependency_context(
    root: Path,
    task: dict,
    sources: dict,
    notes: dict,
) -> dict:
    """Require full context for every repository model explicitly named by a persistence task."""
    enriched = dict(notes)
    dependency = dict(notes.get("dependency_context", {}))
    enriched["dependency_context"] = dependency
    if dependency.get("persistence_contract_requested"):
        contract_text = _task_contract_text(task)
        required = {
            value for value in dependency.get("required_full_model_paths", [])
            if isinstance(value, str)
        }
        for symbol, paths in _repository_model_class_paths(root).items():
            if re.search(rf"\b{re.escape(symbol)}\b", contract_text):
                required.update(paths)

        ordered = sorted(required)
        if len(ordered) > 16:
            dependency["required_full_model_paths"] = ordered[:16]
            dependency["omitted_model_path_count"] = len(ordered) - 16
        else:
            dependency["required_full_model_paths"] = ordered
            dependency["omitted_model_path_count"] = 0
        dependency["missing_full_model_paths"] = [
            path for path in dependency["required_full_model_paths"]
            if path not in sources or sources[path].get("content") is None
        ]

    session_context = _build_live_session_context(root, task, sources)
    if session_context.get("requested"):
        dependency["session_context"] = session_context
        for hints_key in ("literal_hints",):
            while enriched.get(hints_key) and len(json.dumps(enriched).encode("utf-8")) > 8192:
                enriched[hints_key].pop()
                enriched["omitted_literal_count"] = enriched.get("omitted_literal_count", 0) + 1
        while (dependency.get("model_contract_hints")
               and len(json.dumps(enriched).encode("utf-8")) > 8192):
            dependency["model_contract_hints"].pop()
            dependency["omitted_model_contract_hint_count"] = (
                dependency.get("omitted_model_contract_hint_count", 0) + 1
            )
        if len(json.dumps(enriched).encode("utf-8")) > 8192:
            raise ProposalError("live session dependency context exceeds metadata budget: split the task")
    return enriched


def check(root: Path, task_path: Path) -> dict:
    root = root.resolve(strict=True)
    try:
        task, sources = load_task(root, task_path)
    except _TaskBoundaryError as exc:
        return {
            "state": "TASK_BLOCKED",
            "issues": _normalize_readiness_issues([exc.to_fact()]),
        }
    notes = enrich_repository_dependency_context(
        root, task, sources, task_preparation_notes(task, sources)
    )
    validate_task_readiness(notes)
    snapshot = json.dumps({"task": task, "sources": sources}).encode("utf-8")
    return {
        "state": "TASK_READY",
        "task_id": task["task_id"],
        "source_file_count": len(sources),
        "byte_count": len(snapshot),
        "preparation_notes": notes,
    }


def task_preparation_notes(task: dict, sources: dict) -> dict:
    """Advisory only; derived from the immutable task, never a proof identity."""
    try:
        from scripts.two_site_task_context import preparation_notes
    except ModuleNotFoundError:
        from two_site_task_context import preparation_notes
    return preparation_notes(task, sources)


def originally_new_paths(task: dict, sources: dict) -> tuple[str, ...]:
    """Return original absent paths in assignment order, before candidate overlay."""
    return tuple(
        path for path in task["allowed_files"]
        if sources[path]["sha256"] is None and sources[path]["content"] is None
    )


def _new_test_file_limits(task: dict, original_sources: dict) -> dict[str, int]:
    """Classify canonical new tests from original bindings, never drafting hints."""
    try:
        from scripts.two_site_proposal_byte_profile import (
            ProposalByteProfileError, parse_proposal_utf8_budget_profile,
        )
    except ImportError:
        from two_site_proposal_byte_profile import (
            ProposalByteProfileError, parse_proposal_utf8_budget_profile,
        )
    try:
        profile = parse_proposal_utf8_budget_profile(
            task.get("requirements"), task["allowed_files"], task["allowed_files"]
        )
    except ProposalByteProfileError as exc:
        raise ProposalError(str(exc)) from None
    try:
        from scripts.two_site_task_context import _is_python_test_file
    except ImportError:
        from two_site_task_context import _is_python_test_file
    test_only = bool(task["allowed_files"]) and all(
        _is_python_test_file(path) for path in task["allowed_files"]
    )
    limits = {}
    if not isinstance(original_sources, dict):
        raise ProposalError("invalid original source bindings")
    for path in task["allowed_files"]:
        parts = PurePosixPath(path).parts
        if not (test_only or (len(parts) >= 2 and parts[0] == "tests"
                and parts[-1].startswith("test_") and parts[-1].endswith(".py"))):
            continue
        try:
            is_new = classify_source(original_sources[path]) == "new"
        except (KeyError, TransportPolicyError):
            raise ProposalError("invalid original source bindings") from None
        if is_new:
            cap = 24000
            if profile is not None:
                cap = min(64000, profile["file_limits"][path], profile["total_limit"])
            limits[path] = cap
    return limits


def schema(
    role: str,
    task: dict | None = None,
    sources: dict | None = None,
    force_full_paths: tuple[str, ...] = (),
    *,
    original_sources: dict | None = None,
) -> dict:
    if (task is None) != (sources is None) or (task is None and original_sources is not None):
        raise ProposalError("task and sources must both be provided or both omitted")
    fields = {
        "status": {"type": "string", "enum": ["PROPOSAL_READY", "BLOCKED"] if role == "worker" else ["VERIFIED", "CHANGES_REQUIRED", "BLOCKED"]},
        "summary": {"type": "string"},
        "covered_requirements": {"type": "array", "items": {"type": "integer"}},
        "findings": {"type": "array", "items": {"type": "string"}},
        "changes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["path", "original_sha256", "content", "replacements"],
            "properties": {"path": {"type": "string"},
                           "original_sha256": {"type": ["string", "null"]},
                           "content": {"type": ["string", "null"]},
                           "replacements": {
                               "type": "array",
                               "items": {
                                   "type": "object",
                                   "additionalProperties": False,
                                   "required": ["old", "new"],
                                   "properties": {
                                       "old": {"type": "string"},
                                       "new": {"type": "string"},
                                   },
                               },
                           }},
        }},
    }
    if task is not None and sources is not None:
        fields["changes"] = build_changes_schema(
            role, task["allowed_files"], sources, MAX_REPLACEMENTS,
            force_full_paths=force_full_paths,
        )
        if role == "worker":
            origin = sources if original_sources is None else original_sources
            for path, cap in _new_test_file_limits(task, origin).items():
                _apply_new_test_max_length(fields["changes"], {path}, cap)
    return {"type": "object", "additionalProperties": False,
            "required": list(fields), "properties": fields}


def _apply_new_test_max_length(node: object, new_tests: set[str], max_len: int) -> None:
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict) and "content" in props and "path" in props:
            path_schema = props.get("path")
            matched = False
            if isinstance(path_schema, dict):
                const_path = path_schema.get("const")
                enum_paths = path_schema.get("enum")
                if const_path in new_tests:
                    matched = True
                elif isinstance(enum_paths, list) and any(p in new_tests for p in enum_paths):
                    matched = True
                elif path_schema.get("type") == "string":
                    matched = True
            if matched and isinstance(props["content"], dict):
                props["content"]["maxLength"] = min(
                    props["content"].get("maxLength", max_len), max_len
                )
        for val in node.values():
            _apply_new_test_max_length(val, new_tests, max_len)
    elif isinstance(node, list):
        for item in node:
            _apply_new_test_max_length(item, new_tests, max_len)


def normalize_result(
    result: dict,
    task: dict,
    sources: dict,
    role: str,
    strict_transport: bool = False,
    force_full_paths: tuple[str, ...] = (),
) -> dict:
    if type(strict_transport) is not bool:
        raise ProposalError("strict_transport must be a boolean")
    if (not isinstance(force_full_paths, tuple)
            or any(not isinstance(path, str) or not path for path in force_full_paths)
            or len(set(force_full_paths)) != len(force_full_paths)
            or not set(force_full_paths).issubset(task["allowed_files"])):
        raise ProposalError("force_full_paths must be an ordered unique tuple within allowed files")
    forced = set(force_full_paths)
    if not isinstance(result, dict):
        raise ProposalError("incomplete response contract")
    if role == "verifier" or result.get("status") != "PROPOSAL_READY":
        return dict(result)

    changes = result.get("changes")
    if not isinstance(changes, list):
        raise ProposalError("malformed response list")
    if not changes:
        raise ProposalError("plan-only or no-op response is not implementation")

    seen = set()
    normalized_changes = []

    for change in changes:
        if not isinstance(change, dict):
            raise ProposalError("invalid change shape")
        keys = set(change.keys())
        if keys == {"path", "original_sha256", "content"}:
            change_mode = "legacy"
            if not isinstance(change["content"], str):
                raise ProposalError("invalid change shape")
        elif keys == {"path", "original_sha256", "content", "replacements"}:
            content = change["content"]
            replacements = change["replacements"]
            if not (isinstance(content, str) or content is None) or not isinstance(replacements, list):
                raise ProposalError("invalid change shape")
            if isinstance(content, str) and len(replacements) == 0:
                change_mode = "full"
            elif content is None and len(replacements) > 0:
                change_mode = "replacement"
            elif isinstance(content, str) and len(replacements) > 0:
                raise ProposalError("mixed change content and replacements")
            else:
                raise ProposalError("empty change content and replacements")
        else:
            raise ProposalError("invalid change shape")

        name = change["path"]
        if (not isinstance(name, str) or name not in task["allowed_files"]
                or name not in sources or name in seen):
            raise ProposalError("changed path is duplicated or outside assignment")
        seen.add(name)

        if change["original_sha256"] != sources[name]["sha256"]:
            raise ProposalError("proposal is based on different source bytes")

        if strict_transport:
            try:
                validate_change_mode(
                    change, sources[name], MAX_REPLACEMENTS,
                    force_full=name in forced,
                )
            except TransportPolicyError as exc:
                raise ProposalError("proposal transport policy violation") from exc

        if change_mode == "replacement":
            if sources[name]["content"] is None:
                raise ProposalError("new-file replacement is not supported")
            materialized = materialize_replacements(sources[name]["content"], change["replacements"])
            normalized_changes.append({
                "path": name,
                "original_sha256": change["original_sha256"],
                "content": materialized,
            })
        else:
            normalized_changes.append({
                "path": name,
                "original_sha256": change["original_sha256"],
                "content": change["content"],
            })

    normalized_result = dict(result)
    normalized_result["changes"] = normalized_changes
    return normalized_result


# Private provenance for one already-validated terminal rejection; never serialized.
_TERMINAL_NEW_TEST_BUDGET = object()
_PREPARED_MAINTENANCE_RESULT = object()


def validate_result(
    result: dict,
    task: dict,
    sources: dict,
    role: str,
    enforce_raw_transport: bool = True,
    *,
    original_sources: dict | None = None, _prepared_maintenance=None,
) -> dict:
    if _prepared_maintenance is not None and (
            type(_prepared_maintenance) is not tuple or len(_prepared_maintenance) != 2
            or _prepared_maintenance[0] is not _PREPARED_MAINTENANCE_RESULT):
        raise ProposalError("invalid prepared maintenance result capability")
    if type(enforce_raw_transport) is not bool:
        raise ProposalError("enforce_raw_transport must be a boolean")
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
    origin = sources if original_sources is None else original_sources
    if _prepared_maintenance is not None:
        new_test_limits = _prepared_maintenance[1] if role == "worker" else {}
        notes = {"output_contract": {"test_only_task": False}}
    else:
        new_test_limits = _new_test_file_limits(task, origin) if role == "worker" else {}
        notes = task_preparation_notes(task, sources)
    out_contract = notes.get("output_contract", {}) if isinstance(notes, dict) else {}
    test_only = bool(out_contract.get("test_only_task"))
    max_transport = out_contract.get("max_test_only_transport_bytes", 64000)

    if role == "worker" and test_only and enforce_raw_transport:
        raw_transport_bytes = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        if raw_transport_bytes > max_transport:
            raise ProposalError("test-only raw transport exceeds budget")

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
        if name in new_test_limits:
            actual_bytes = len(change["content"].encode("utf-8"))
            if actual_bytes > new_test_limits[name]:
                error = ProposalError(f"new test file content exceeds budget: {name}")
                error._terminal_new_test_budget = (
                    _TERMINAL_NEW_TEST_BUDGET, name, new_test_limits[name], actual_bytes,
                )
                raise error
        if role == "worker" and success and name.endswith(".py"):
            try:
                compile(change["content"], name, "exec", dont_inherit=True)
            except SyntaxError as exc:
                line = exc.lineno if isinstance(exc.lineno, int) else 0
                column = exc.offset if isinstance(exc.offset, int) else 0
                message = (exc.msg or "syntax error").replace("\n", " ")[:160]
                raise ProposalError(
                    f"generated Python syntax is invalid: {name}:{line}:{column}: {message}"
                ) from exc
    return result



def validate_prepared_result(root, authorization, result, task, sources, role, *, source_head):
    """Closed maintenance-only materialization; never change ordinary budgets."""
    from scripts.two_site_qoder_maintenance import validate_maintenance_authorization
    validate_maintenance_authorization(root, authorization, task=task, sources=sources,
                                       source_head=source_head, evidence_only=True)
    limits = {path: 64000 for path in task["allowed_files"]
              if path.startswith("tests/") and path.endswith(".py")
              and sources[path]["content"] is None}
    value = validate_result(result, task, sources, role, enforce_raw_transport=False,
                            _prepared_maintenance=(_PREPARED_MAINTENANCE_RESULT, limits))
    if len(serialize_json(value)) > 320000:
        raise ProposalError("prepared maintenance materialization exceeds 320000 bytes")
    return value



def build_prepared_review_transport(root, authorization, candidate, task, sources, *, source_head,
                                    proposal_sha256, raw_transport):
    """Complete original once plus actual proved unique hunks, maintenance only."""
    if (not isinstance(proposal_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", proposal_sha256)
            or sha(serialize_json(candidate)) != proposal_sha256
            or len(serialize_json(raw_transport)) > MAX_CONTEXT
            or type(raw_transport) is not dict or type(raw_transport.get("changes")) is not list
            or any(type(change) is not dict or set(change) != {"path", "original_sha256", "content", "replacements"}
                   for change in raw_transport["changes"])):
        raise ProposalError("prepared maintenance raw delta or canonical proposal binding is invalid")
    normalized = normalize_result(raw_transport, task, sources, "worker", strict_transport=True)
    validated = validate_prepared_result(root, authorization, normalized, task, sources, "worker", source_head=source_head)
    if validated != candidate:
        raise ProposalError("prepared maintenance raw delta does not materialize the reviewed proposal")
    return {"canonical_proposal_sha256": proposal_sha256, "raw_worker_transport": raw_transport}

def worker_result_limit(root, directory):
    """Historical replay may read 320k only for an independently validated job."""
    _, snapshot = read_task_snapshot(root, directory / "task.json", "worker task snapshot")
    authorization = snapshot.get("maintenance_authorization")
    if authorization is None:
        return MAX_CONTEXT
    from scripts.two_site_qoder_maintenance import validate_maintenance_authorization
    validate_maintenance_authorization(root, authorization, task=snapshot["task"],
        sources=snapshot["sources"], source_head=snapshot["source_head"], evidence_only=True)
    return 320000

def load_prior_worker(
    root: Path,
    proposal_path: Path,
    proposal_text: str,
    task: dict,
    sources: dict,
    head: str,
) -> tuple[dict, dict, str]:
    """Bind a review/correction to one complete worker run and exact proposal bytes."""
    _, baseline = read_task_snapshot(root, proposal_path.with_name("task.json"), "worker snapshot")
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
    authorization = baseline.get("maintenance_authorization")
    candidate = (validate_result(unique_json(proposal_text), task, sources, "worker", enforce_raw_transport=False)
                 if authorization is None else validate_prepared_result(root, authorization,
                     unique_json(proposal_text), task, sources, "worker", source_head=head))
    if candidate["status"] != "PROPOSAL_READY":
        raise ProposalError("review/correction requires a complete worker proposal")
    validate_generation_completion(
        root, proposal_path.parent, task=task, sources=sources, role="worker",
        status=worker_status, result=candidate,
    )
    return candidate, worker_status, proposal_sha256


def _load_bound_opus_fallback_receipt(
    root: Path,
    failed_id: str,
    task: dict,
    sources: dict,
    head: str,
    controller_id: str,
) -> dict:
    if (
        not isinstance(failed_id, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", failed_id)
    ):
        raise ProposalError("invalid failed run ID")
    if not isinstance(controller_id, str) or not controller_id.strip():
        raise ProposalError("nonempty controller ID required for fallback")

    failed_dir = root / ".local/two-site-refactor/proposals" / failed_id
    status_raw = bounded_file_bytes(root, failed_dir / "status.json", MAX_CONTEXT, "failed status")
    task_raw, _ = read_task_snapshot(root, failed_dir / "task.json", "failed task")
    stream_raw = bounded_file_bytes(root, failed_dir / "stream.jsonl", MAX_OUTPUT, "failed stream")

    try:
        receipt = validate_opus_failure_receipt(
            status_raw,
            task_raw,
            stream_raw,
            expected_run_id=failed_id,
            expected_task=task,
            expected_sources=sources,
            expected_head=head,
        )
    except OpusReceiptValidationError as exc:
        raise ProposalError(f"invalid Opus failure receipt: {exc}") from exc

    status_obj = unique_json(status_raw.decode("utf-8"))
    if not isinstance(status_obj, dict) or status_obj.get("controller_run_id") != controller_id:
        raise ProposalError("failed run controller run ID does not match current controller")

    return receipt


def validate_correction_feedback(text: str, proposal_sha256: str) -> dict:
    feedback = unique_json(text)
    required = {"reviewer", "reviewed_proposal_sha256", "findings", "test_evidence"}
    if not isinstance(feedback, dict) or set(feedback) != required:
        raise ProposalError("correction feedback does not match the exact contract")
    if feedback["reviewer"] not in {"gemini", "qoder", "codex", "test-gate"}:
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


def _is_explicit_forbidden_evidence(obj: object) -> bool:
    if not isinstance(obj, dict):
        return False
    tool_name = obj.get("tool_name")
    if isinstance(tool_name, str) and tool_name and tool_name != "finish":
        return True
    tool_info = obj.get("tool_info")
    if isinstance(tool_info, dict):
        name = tool_info.get("name")
        if isinstance(name, str) and name and name != "finish":
            return True
    return bool("subagent_info" in obj or obj.get("step_type") == "subagent")


def _has_forbidden_stream_evidence(events: list) -> bool:
    for event in events:
        if not isinstance(event, dict):
            continue
        if _is_explicit_forbidden_evidence(event):
            return True
        if event.get("event") == "step_update":
            step = event.get("step_update")
            if _is_explicit_forbidden_evidence(step):
                return True
    return False


class _FinishLifecycleValidator:
    def __init__(self, session: str) -> None:
        self.session = session
        self.mode: str | None = None
        self.legacy_count = 0
        self.distinct_indexes: list[int] = []
        self.current_index: int | None = None
        self.current_state: str | None = None
        self.current_params: dict | None = None

    def record_step(self, event: dict, step: dict) -> None:
        if _is_explicit_forbidden_evidence(step):
            raise ForbiddenToolAttemptError("tool attempt invalidates a proposal-only run")
        if (
            step.get("step_type") != "tool"
            or step.get("tool_name") != "finish"
            or "subagent_info" in step
        ):
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")

        step_cid = step.get("conversation_id")
        event_cid = event.get("conversation_id")
        if (step_cid is not None and step_cid != self.session) or (
            event_cid is not None and event_cid != self.session
        ):
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")

        has_state = "state" in step
        has_index = "step_index" in step

        if not has_state and not has_index:
            if self.mode is not None and self.mode != "legacy":
                raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
            if self.legacy_count >= 1:
                raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
            tool_info = step.get("tool_info")
            if (
                not isinstance(tool_info, dict)
                or set(tool_info.keys()) != {"name", "parameters"}
                or tool_info.get("name") != "finish"
            ):
                raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
            params = tool_info.get("parameters")
            if (
                not isinstance(params, dict)
                or set(params.keys()) != CANONICAL_RESPONSE_KEYS
            ):
                raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
            if not set(step.keys()).issubset({"step_type", "tool_name", "tool_info", "conversation_id"}):
                raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
            self.mode = "legacy"
            self.legacy_count += 1
            return

        if self.mode is not None and self.mode != "lifecycle":
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
        if not (has_state and has_index):
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")

        step_index = step.get("step_index")
        if type(step_index) is not int or isinstance(step_index, bool) or step_index <= 0:
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")

        state = step.get("state")
        if state not in {"ACTIVE", "ERROR"}:
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")

        if not set(step.keys()).issubset({
            "step_type", "tool_name", "step_index", "state", "tool_info",
            "conversation_id", "duration_seconds",
        }):
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")

        if "duration_seconds" in step:
            dur = step["duration_seconds"]
            if type(dur) not in (int, float) or isinstance(dur, bool) or dur < 0:
                raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")

        tool_info = step.get("tool_info")
        if not isinstance(tool_info, dict) or tool_info.get("name") != "finish":
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")

        if state == "ACTIVE":
            if set(tool_info.keys()) != {"name", "parameters"}:
                raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
            params = tool_info.get("parameters")
            if not isinstance(params, dict) or set(params.keys()) != CANONICAL_RESPONSE_KEYS:
                raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
            if self.current_state == "ACTIVE":
                raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
            if self.current_index is not None and step_index <= self.current_index:
                raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
            if len(self.distinct_indexes) >= 3:
                raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
            self.mode = "lifecycle"
            self.distinct_indexes.append(step_index)
            self.current_index = step_index
            self.current_state = "ACTIVE"
            self.current_params = params
            return

        if self.current_state != "ACTIVE":
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
        if step_index != self.current_index:
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
        if set(tool_info.keys()) != {"name", "parameters", "error"}:
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
        params = tool_info.get("parameters")
        if params != self.current_params:
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
        err = tool_info.get("error")
        if (
            not isinstance(err, dict)
            or set(err.keys()) != {"type", "message"}
            or err.get("type") != "TOOL_ERROR"
            or not isinstance(err.get("message"), str)
            or len(err["message"].encode("utf-8")) > 4096
        ):
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
        self.current_state = "ERROR"


def parse_stream(raw: str, task: dict, sources: dict, role: str) -> tuple[dict, dict]:
    return _parse_stream(raw, task, sources, role)


def _parse_stream(
    raw: str,
    task: dict,
    sources: dict,
    role: str,
    force_full_paths: tuple[str, ...] = (),
    expected_model: str = MODEL,
    *,
    original_sources: dict | None = None,
) -> tuple[dict, dict]:
    events = [unique_json(line) for line in raw.splitlines() if line.strip()]
    if any(not isinstance(e, dict) for e in events):
        raise ProposalError("CLI event must be an object")
    for event in events:
        kind = event.get("event")
        if kind in {"init", "result", "step_update"} and not isinstance(event.get(kind), dict):
            raise ProposalError("malformed CLI event envelope")
    init = [e for e in events if e.get("event") == "init"]
    terminal = [e["result"] for e in events if e.get("event") == "result"]
    if len(init) != 1 or init[0]["init"].get("model") != expected_model or len(terminal) != 1:
        raise ProposalError("missing unique model/session/result evidence")
    envelope = terminal[0]
    session = init[0].get("conversation_id")
    if (not isinstance(session, str) or not session.strip()
            or envelope.get("conversation_id") != session):
        raise ProposalError("CLI did not complete one successful session")
    if envelope.get("denied_actions"):
        if _has_forbidden_stream_evidence(events):
            raise ForbiddenToolAttemptError("tool attempt invalidates a proposal-only run")
        raise RetryableProviderSessionError("CLI did not complete one successful session")
    if envelope.get("status") != "SUCCESS":
        error = envelope.get("error")
        if is_retryable_interrupted_stream(raw, expected_model):
            raise RetryableProviderSessionError(
                "provider stream interrupted before a complete result"
            )
        if (isinstance(error, str)
                and not re.search(
                    r"auth|quota|rate[\s_-]*limit|time[\s_-]*out|timed[\s_-]*out|deadline|network|connect",
                    error, re.IGNORECASE,
                )
                and re.search(
                    r"MODEL_OUTPUT_TRUNCATED|"
                    r"exceed.*output[-\s]+tokens?[-\s]+limit|"
                    r"output[-\s]+tokens?[-\s]+limit.*exceed",
                    error, re.IGNORECASE | re.DOTALL,
                )):
            raise ProposalError("MODEL_OUTPUT_TRUNCATED: rescope to a smaller task")
        if (isinstance(error, str)
                and not re.search(
                    r"auth|quota|rate[\s_-]*limit|time[\s_-]*out|timed[\s_-]*out|deadline|network|connect",
                    error, re.IGNORECASE,
                )
                and re.search(r"improperly formatted function call", error, re.IGNORECASE)):
            raise RetryableProviderOutputError(
                "provider returned an invalid response envelope"
            )
        raise ProposalError("CLI did not complete one successful session")

    validator = _FinishLifecycleValidator(session)
    for event in events:
        if "conversation_id" in event and event.get("conversation_id") != session:
            raise ProposalError("CLI did not complete one successful session")
        if _is_explicit_forbidden_evidence(event):
            raise ForbiddenToolAttemptError("tool attempt invalidates a proposal-only run")
        if (
            "tool_name" in event
            or "tool_info" in event
            or "subagent_info" in event
            or event.get("step_type") in {"tool", "subagent"}
        ):
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
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
        if _is_explicit_forbidden_evidence(step):
            raise ForbiddenToolAttemptError("tool attempt invalidates a proposal-only run")
        validator.record_step(event, step)

    # With --json-schema the documented machine payload is structured_output.
    # CLI 1.1.27 may also append its submission object to free-text response.
    # Never scrape that text or fall back when a supplied structured value is bad.
    enforce_raw_transport = True
    if "structured_output" in envelope:
        result = envelope["structured_output"]
        if role == "worker":
            notes = task_preparation_notes(task, sources)
            out_contract = notes.get("output_contract", {}) if isinstance(notes, dict) else {}
            if out_contract.get("test_only_task"):
                max_transport = out_contract.get("max_test_only_transport_bytes", 64000)
                raw_transport_bytes = len(
                    json.dumps(result, ensure_ascii=False).encode("utf-8")
                )
                if raw_transport_bytes > max_transport:
                    raise ProposalError("test-only raw transport exceeds budget")
            enforce_raw_transport = False
    else:
        if not isinstance(envelope.get("response"), str):
            raise RetryableProviderSessionError("CLI result has no response text")
        try:
            result = unique_json(envelope["response"])
            if role == "worker":
                notes = task_preparation_notes(task, sources)
                out_contract = notes.get("output_contract", {}) if isinstance(notes, dict) else {}
                if out_contract.get("test_only_task"):
                    max_transport = out_contract.get("max_test_only_transport_bytes", 64000)
                    if len(envelope["response"].encode("utf-8")) > max_transport:
                        raise ProposalError("test-only raw transport exceeds budget")
                enforce_raw_transport = False
        except json.JSONDecodeError as exc:
            if not exc.msg.startswith("Extra data"):
                raise RetryableProviderSessionError(
                    "CLI response is not one valid JSON document"
                ) from exc
            if role == "worker":
                result, first_span = _deduplicate_response_with_first_document_span(
                    envelope["response"]
                )
                notes = task_preparation_notes(task, sources)
                out_contract = notes.get("output_contract", {}) if isinstance(notes, dict) else {}
                if out_contract.get("test_only_task"):
                    max_transport = out_contract.get("max_test_only_transport_bytes", 64000)
                    if len(first_span) > max_transport:
                        raise ProposalError("test-only raw transport exceeds budget")
                enforce_raw_transport = False
            else:
                result = _deduplicate_response(envelope["response"])
    try:
        result = normalize_result(
            result, task, sources, role, strict_transport=True,
            force_full_paths=force_full_paths,
        )
        result = validate_result(
            result, task, sources, role, enforce_raw_transport=enforce_raw_transport,
            original_sources=original_sources,
        )
    except ProposalError as exc:
        if role == "worker" and _provider_output_is_retryable(exc):
            raise RetryableProviderOutputError(
                "provider returned unusable proposal transport"
            ) from exc
        raise
    if len(serialize_json(result)) > MAX_CONTEXT:
        raise ProposalError("canonical proposal exceeds the safe context budget")
    return result, envelope


def _validate_final_generation_frame(
    raw: str, task: dict, sources: dict, role: str,
    force_full_paths: tuple[str, ...], expected_model: str, expected_cid: str,
    *, original_sources: dict | None = None,
) -> tuple[dict, dict]:
    """Recovery-only finality; advertised init.tools is discovery, not execution."""
    if not raw.endswith("\n"):
        raise ProposalError("recovery requires a complete final NDJSON frame")
    events = [unique_json(line) for line in raw.splitlines() if line.strip()]
    if not events or any(type(event) is not dict for event in events):
        raise ProposalError("recovery requires complete event objects")
    initials = [event for event in events if event.get("event") == "init"]
    terminals = [event for event in events if event.get("event") == "result"]
    if (len(initials) != 1 or events[0] is not initials[0]
            or len(terminals) != 1 or events[-1] is not terminals[0]
            or initials[0].get("conversation_id") != expected_cid
            or type(initials[0].get("init")) is not dict
            or initials[0]["init"].get("model") != expected_model):
        raise ProposalError("recovery final frame identity or ordering mismatch")
    envelope = terminals[0].get("result")
    if (type(envelope) is not dict or envelope.get("status") != "SUCCESS"
            or type(envelope.get("num_turns")) is not int
            or envelope["num_turns"] != 1
            or envelope.get("conversation_id") != expected_cid):
        raise ProposalError("recovery requires exactly one successful completed turn")
    for event in events:
        # Do not confuse advertised tools/resources in init with attempted tools.
        containers = [event]
        step = event.get("step_update")
        if type(step) is dict:
            containers.append(step)
        if event is terminals[0]:
            containers.append(envelope)
        for item in containers:
            if (item.get("denied_actions")
                    or any(key in item for key in ("tool_name", "tool_info", "subagent_info"))
                    or item.get("step_type") in {"tool", "subagent"}):
                raise ProposalError("tool or denied action invalidates recovered generation")
    return _parse_stream(
        raw, task, sources, role, force_full_paths, expected_model,
        original_sources=original_sources,
    )



def _raise_bound_output_limit_error(
    stdout: bytes, task: dict, sources: dict, role: str,
    force_full_paths: tuple[str, ...], expected_model: str, expected_cid: str,
    *, original_sources: dict | None = None,
) -> None:
    """Reject one fully bound real ERROR; never make it recovery eligible."""
    try:
        raw = stdout.decode("utf-8")
        if not raw.endswith("\n"):
            return
        events = [unique_json(line) for line in raw.splitlines() if line.strip()]
        json.dumps(events, allow_nan=False)  # A malformed SDK number grants no shortcut.
        if not events or any(type(event) is not dict for event in events):
            return
        initials = [event for event in events if event.get("event") == "init"]
        terminals = [event for event in events if event.get("event") == "result"]
        if (len(initials) != 1 or events[0] is not initials[0]
                or len(terminals) != 1 or events[-1] is not terminals[0]):
            return
        init, terminal = initials[0], terminals[0]
        envelope = terminal.get("result")
        if (type(init.get("init")) is not dict
                or init.get("conversation_id") != expected_cid
                or init["init"].get("model") != expected_model
                or type(envelope) is not dict or envelope.get("status") != "ERROR"
                or envelope.get("conversation_id") != expected_cid
                or type(envelope.get("num_turns")) is not int or envelope["num_turns"] != 1):
            return
        expected = schema(role, task, sources, force_full_paths,
                          original_sources=original_sources)
        if any(not _exact_equal(obj.get("json_schema"), expected)
               for obj in (init["init"], envelope)):
            return
        for event in events:
            kind = event.get("event")
            if kind in {"init", "result", "step_update"} and type(event.get(kind)) is not dict:
                return
            if kind in {"error", "denied", "permission_denied"}:
                return
            for obj in (event, event.get("step_update"), envelope if event is terminal else None):
                if type(obj) is not dict:
                    continue
                if (("conversation_id" in obj and obj["conversation_id"] != expected_cid)
                        or ("model" in obj and obj["model"] != expected_model)
                        or obj.get("denied_actions")
                        or any(k in obj for k in ("tool_name", "tool_info", "subagent_info"))
                        or obj.get("step_type") in {"tool", "subagent"}):
                    return
    except (ProposalError, UnicodeError, TypeError, ValueError):
        return
    try:
        _parse_stream(raw, task, sources, role, force_full_paths, expected_model,
                      original_sources=original_sources)
    except ProposalError as error:
        if (type(error) is ProposalError
                and str(error) == "MODEL_OUTPUT_TRUNCATED: rescope to a smaller task"):
            raise


def _generation_final_frame_check(
    stdout: bytes, stderr: bytes, task: dict, sources: dict, role: str,
    force_full_paths: tuple[str, ...], expected_model: str, expected_cid: str,
    *, original_sources: dict | None = None,
) -> bool:
    """Keep recovery closed; promptly reject bound output limits or byte gates."""
    if stderr:
        return False
    _raise_bound_output_limit_error(
        stdout, task, sources, role, force_full_paths, expected_model, expected_cid,
        original_sources=original_sources,
    )
    try:
        _validate_final_generation_frame(
            stdout.decode("utf-8"), task, sources, role, force_full_paths,
            expected_model, expected_cid, original_sources=original_sources,
        )
    except ProposalError as error:
        if type(error) is ProposalError and role == "worker":
            evidence = error.__dict__.get("_terminal_new_test_budget")
            if (type(evidence) is tuple and len(evidence) == 4
                    and evidence[0] is _TERMINAL_NEW_TEST_BUDGET
                    and type(evidence[1]) is str
                    and type(evidence[2]) is int and type(evidence[3]) is int
                    and 0 < evidence[2] < evidence[3]
                    and str(error) == "new test file content exceeds budget: " + evidence[1]):
                origin = sources if original_sources is None else original_sources
                try:
                    limits = _new_test_file_limits(task, origin)
                except ProposalError:
                    return False
                if limits.get(evidence[1]) == evidence[2]:
                    raise
        return False
    except UnicodeError:
        return False
    return True


_GENERATION_PROOF_NAME = "transport-completion.json"
_GENERATION_PROOF_LIMIT = 16 * 1024


def _generation_contract_sha(root: Path) -> str:
    contract = validate_runtime_contract(root)
    if (contract.get("schema_version") != "1.3"
            or contract.get("completion_policy") != GENERATION_COMPLETION_POLICY):
        raise ProposalError("current generation completion contract is required")
    return sha(bounded_file_bytes(
        root, root / GEMINI_RUNTIME_CONTRACT_REL, 16 * 1024,
        "generation completion runtime contract",
    ))


def _generation_artifact_bindings(
    root: Path, run_dir: Path, *, task: dict, sources: dict, role: str,
    status: dict, result: dict, observations: dict,
) -> dict:
    """Recompute retained bytes/schema/correction context; no historical OS query."""
    if role not in {"worker", "verifier"}:
        raise ProposalError("invalid completion proof role")
    snapshot_bytes, snapshot = read_task_snapshot(root, run_dir / "task.json", "completion task snapshot")
    if (type(snapshot) is not dict or snapshot.get("task") != task
            or snapshot.get("sources") != sources or snapshot.get("source_head") != status.get("head")):
        raise ProposalError("completion task snapshot binding mismatch")
    if snapshot.get("maintenance_authorization") is not None:
        from scripts.two_site_qoder_maintenance import validate_maintenance_authorization
        validate_maintenance_authorization(root, snapshot["maintenance_authorization"], task=task,
            sources=sources, source_head=status.get("head"), evidence_only=True)
    prompt_bytes = bounded_file_bytes(root, run_dir / "prompt.txt", 2 * MAX_CONTEXT,
                                     "completion logical prompt")
    compact = status.get("compact_review")
    if type(compact) is not bool:
        raise ProposalError("completion compact-review binding is missing")
    framing = None
    framing_path = run_dir / _FRAMED_MANIFEST
    if status.get("input_transport") == _FRAMED_INPUT:
        if compact:
            raise ProposalError("compact review cannot use framed input")
        framing, wires = _framed_input_plan(prompt_bytes.decode("utf-8"))
        retained = bounded_file_bytes(root, framing_path, 8192, "input framing manifest")
        if retained != serialize_json(framing) or sha(retained) != status.get("input_framing_sha256"):
            raise ProposalError("completion input-framing binding mismatch")
        wire = b"".join(wires)
    else:
        if "input_transport" in status or "input_framing_sha256" in status or framing_path.exists():
            raise ProposalError("completion input-framing mode is missing or invalid")
        wire = _cli_stream_input(prompt_bytes.decode("utf-8"), is_compact_review=compact)
    stdout = bounded_file_bytes(root, run_dir / "stream.jsonl", MAX_OUTPUT,
                                "completion retained stdout")
    stderr = bounded_file_bytes(root, run_dir / "stderr.txt", MAX_OUTPUT,
                                "completion retained stderr")
    if len(stdout) + len(stderr) > MAX_OUTPUT:
        raise ProposalError("completion combined output exceeds budget")
    try:
        normalized_observations = validate_observations(observations, wire, stdout, stderr)
    except GenerationCompletionError as exc:
        raise ProposalError(str(exc)) from None
    result_bytes = bounded_file_bytes(root, run_dir / "result.json", MAX_CONTEXT,
                                     "completion result")
    if unique_json(result_bytes.decode("utf-8")) != result:
        raise ProposalError("completion result bytes mismatch")
    schema_bytes = bounded_file_bytes(root, run_dir / "response-schema.json", MAX_CONTEXT,
                                     "completion response schema")
    if sha(schema_bytes) != status.get("response_schema_sha256"):
        raise ProposalError("completion dispatched response-schema bytes changed")
    active_sources, force_full_paths = sources, ()
    candidate_sha = delta_sha = None
    prior_identity = None
    if role == "worker" and "feedback" in snapshot:
        if type(snapshot.get("proposal")) is not str:
            raise ProposalError("completion correction prior proposal is missing")
        prior = unique_json(snapshot["proposal"])
        validate_result(prior, task, sources, "worker", enforce_raw_transport=False,
                        original_sources=sources)
        prior_sha = sha(snapshot["proposal"].encode("utf-8"))
        if type(snapshot["feedback"]) is not dict:
            raise ProposalError("completion correction feedback is not normalized")
        validate_correction_feedback(serialize_json(snapshot["feedback"]).decode("utf-8"), prior_sha)
        active_sources = build_candidate_sources(task["allowed_files"], sources, prior["changes"])
        candidate_sha = sha(serialize_json(active_sources))
        force_full_paths = originally_new_paths(task, sources)
        prior_identity = {
            "run_id": status.get("input_worker_run_id"),
            "conversation_id": status.get("input_worker_conversation_id"),
            "proposal_sha256": prior_sha,
        }
        if (not all(type(prior_identity[key]) is str and prior_identity[key]
                    for key in ("run_id", "conversation_id"))
                or prior_identity["run_id"] == status.get("run_id")
                or prior_identity["conversation_id"] == status.get("conversation_id")
                or status.get("input_proposal_sha256") != prior_sha
                or status.get("candidate_sources_sha256") != candidate_sha):
            raise ProposalError("completion correction candidate/prior binding mismatch")
    expected_schema = schema(
        role, task, active_sources, force_full_paths, original_sources=sources,
    )
    if unique_json(schema_bytes.decode("utf-8")) != expected_schema:
        raise ProposalError("completion response schema changed")
    if (type(status.get("cli_returncode")) is not int
            or status["cli_returncode"] != normalized_observations["actual_returncode"]
            or status.get("cli_normal_exit_observed") is not normalized_observations["normal_exit_observed"]
            or status.get("generation_completion_outcome") != normalized_observations["outcome"]):
        raise ProposalError("completion status contradicts captured lifecycle")
    if framing is not None:
        if normalized_observations["outcome"] != "NORMAL_EXIT":
            raise ProposalError("framed input requires a real normal exit")
        raw_result, envelope = _parse_framed_stream(
            stdout.decode("utf-8"), framing, task, active_sources, role, force_full_paths,
            status.get("model"), status.get("conversation_id"), original_sources=sources,
        )
    elif normalized_observations["outcome"] == "RECOVERED_COMPLETED_RESULT":
        if stderr:
            raise ProposalError("stderr invalidates recovered generation")
        raw_result, envelope = _validate_final_generation_frame(
            stdout.decode("utf-8"), task, active_sources, role, force_full_paths,
            status.get("model"), status.get("conversation_id"), original_sources=sources,
        )
    else:
        raw_result, envelope = _parse_stream(
            stdout.decode("utf-8"), task, active_sources, role, force_full_paths,
            status.get("model"), original_sources=sources,
        )
    if envelope.get("conversation_id") != status.get("conversation_id"):
        raise ProposalError("completion prepared session differs from final stream")
    expected_result = raw_result
    if candidate_sha is not None:
        delta_bytes = bounded_file_bytes(root, run_dir / "delta-result.json", MAX_CONTEXT,
                                        "completion correction delta")
        if unique_json(delta_bytes.decode("utf-8")) != raw_result:
            raise ProposalError("completion raw correction delta mismatch")
        delta_sha = sha(delta_bytes)
        expected_result = rebase_correction_result(
            raw_result, task["allowed_files"], sources, active_sources,
        )
        validate_result(expected_result, task, sources, role, enforce_raw_transport=False,
                        original_sources=sources)
        if status.get("delta_result_sha256") != delta_sha:
            raise ProposalError("completion correction delta SHA mismatch")
    if expected_result != result:
        raise ProposalError("completion normalized/rebased result mismatch")
    profile = status.get("runtime_profile")
    if (type(profile) is not dict
            or profile.get("conversation_id") != status.get("conversation_id")
            or type(profile.get("native_tools")) is not int or profile["native_tools"] != 0
            or profile.get("command_policy") != "off"
            or profile.get("actual_executor_checked") is not True):
        raise ProposalError("completion supported runtime-profile evidence is missing")
    cli = status.get("cli")
    if (type(cli) is not dict or cli.get("version") != EXPECTED_CLI_VERSION
            or cli.get("sha256") != EXPECTED_CLI_SHA256):
        raise ProposalError("completion CLI pin differs from current contract")
    requested_effort = status.get("generation_effort_requested")
    effort_present = status.get("effort_arg_present")
    if (type(effort_present) is not bool
            or status.get("prepared_effort") != requested_effort
            or (effort_present and (type(requested_effort) is not str
                                    or requested_effort != status.get("effort")))
            or (not effort_present and requested_effort is not None)):
        raise ProposalError("completion requested/profile effort binding is inconsistent")
    bound_role = bound_gemini_role(status, role)
    if bound_role is not None:
        recovery_model = bound_role["model"]
        recovery_effort = bound_role["reasoning_effort"]
        if (status.get("model") != recovery_model or status.get("effort") != recovery_effort):
            raise ProposalError("completion selected role differs from its model configuration snapshot")
    else:
        # The unchanged runtime contract identifies the original role settings
        # for retained proofs predating the shared configuration snapshot.
        recovery_model, recovery_effort = (
            (RUNTIME_CONTRACT["worker_model"], RUNTIME_CONTRACT["worker_effort"])
            if role == "worker" else (RUNTIME_CONTRACT["model"], RUNTIME_CONTRACT["effort"])
        )
    if (normalized_observations["outcome"] == "RECOVERED_COMPLETED_RESULT"
            and (status.get("model") != recovery_model
                 or status.get("prepared_effort") != recovery_effort
                 or status.get("generation_effort_requested") != recovery_effort
                 or status.get("effort") != recovery_effort
                 or status.get("effort_arg_present") is not True)):
        raise ProposalError("recovery requires the pinned Gemini role/profile effort")
    proof = {
        "schema_version": "1.0", "kind": "GENERATION_COMPLETION_V1",
        "policy": GENERATION_COMPLETION_POLICY,
        "runtime_contract_sha256": _generation_contract_sha(root),
        "run_id": status.get("run_id"), "role": role, "head": status.get("head"),
        "conversation_id": status.get("conversation_id"), "model": status.get("model"),
        "requested_effort": status.get("generation_effort_requested"),
        "selected_policy_effort": status.get("effort"),
        "prepared_effort": status.get("prepared_effort"),
        "effort_arg_present": status.get("effort_arg_present"),
        "runtime_profile": profile, "cli": cli, "compact_review": compact,
        "task_snapshot_sha256": sha(snapshot_bytes), "prompt_sha256": sha(prompt_bytes),
        "response_schema_sha256": sha(schema_bytes),
        "raw_result_sha256": sha(serialize_json(raw_result)),
        "candidate_sources_sha256": candidate_sha, "prior_worker_identity": prior_identity,
        "delta_result_sha256": delta_sha, "result_sha256": sha(result_bytes),
        "observations": normalized_observations,
        "outer_context_exited_normally": True,
    }
    if bound_role is not None:
        proof["model_config_sha256"] = status["model_config_sha256"]
    if framing is not None:
        proof["input_framing"] = framing
    return proof


def _publish_generation_proof(path: Path, proof: dict) -> str:
    """One-shot bounded proof, after normal context exit; never status self-hash."""
    body = serialize_json(proof)
    if len(body) > _GENERATION_PROOF_LIMIT:
        raise ProposalError("generation completion proof exceeds budget")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    primary = None
    try:
        remaining = memoryview(body)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise ProposalError("generation completion proof short write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    except BaseException as exc:
        primary = exc
    finally:
        try:
            os.close(descriptor)
        except BaseException as exc:
            if primary is None:
                primary = exc
    if primary is not None:
        raise primary
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    except BaseException as exc:
        primary = exc
    finally:
        try:
            os.close(directory)
        except BaseException as exc:
            if primary is None:
                primary = exc
    if primary is not None:
        raise primary
    return sha(body)


def validate_generation_completion(
    root: Path, run_dir: Path, *, task: dict, sources: dict, role: str,
    status: dict, result: dict,
) -> dict:
    """Mandatory provider-specific completion; historical Gemini proof stays exact."""
    if type(status) is dict and status.get("author_provider") == "qoder":
        if role != "worker":
            raise ProposalError("Qoder author proof cannot replace independent Gemini review")
        from scripts.two_site_qoder_author import validate_author_completion
        return validate_author_completion(root, run_dir, task=task, sources=sources,
                                          status=status, result=result)
    if type(status) is dict and status.get("author_provider") not in (None, "antigravity"):
        raise ProposalError("unknown proposal author provider")
    contract_sha = _generation_contract_sha(root)
    if (type(status) is not dict or status.get("completion_policy") != GENERATION_COMPLETION_POLICY
            or status.get("runtime_contract_sha256") != contract_sha
            or not isinstance(status.get("completion_proof_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", status["completion_proof_sha256"])):
        raise ProposalError("current generation completion proof is required")
    path = run_dir / _GENERATION_PROOF_NAME
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.geteuid() or before.st_nlink != 1):
        raise ProposalError("unsafe generation completion proof")
    raw = bounded_file_bytes(root, path, _GENERATION_PROOF_LIMIT, "generation completion proof")
    after = path.lstat()
    if (file_fingerprint(before) != file_fingerprint(after)
            or sha(raw) != status["completion_proof_sha256"]):
        raise ProposalError("generation completion proof drift")
    proof = unique_json(raw.decode("utf-8"))
    if type(proof) is not dict or serialize_json(proof) != raw:
        raise ProposalError("noncanonical generation completion proof")
    expected = _generation_artifact_bindings(
        root, run_dir, task=task, sources=sources, role=role,
        status=status, result=result, observations=proof.get("observations"),
    )
    if (proof.get("outer_context_exited_normally") is not True
            or proof != expected or status.get("result_sha256") != expected["result_sha256"]):
        raise ProposalError("generation completion proof binding mismatch")
    return expected


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


try:
    from scripts.two_site_proposal_runtime_profile import (
        PreparedProfile, ProposalProfileError, prepare_profile, validate_profile,
        proposal_hooks,
    )
except ModuleNotFoundError:
    from two_site_proposal_runtime_profile import (
        PreparedProfile, ProposalProfileError, prepare_profile, validate_profile,
        proposal_hooks,
    )


def serialize_json(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


MAINTENANCE_SNAPSHOT_BYTES = 3 * 320000


def task_snapshot_limit(root: Path | None, material: dict) -> int:
    """Larger retained metadata is authority-bound, never a provider input cap."""
    if type(material) is not dict:
        raise ProposalError("invalid task snapshot")
    authorization = material.get("maintenance_authorization")
    if authorization is None or root is None:
        return 3 * MAX_CONTEXT
    from scripts.two_site_qoder_maintenance import validate_maintenance_authorization
    validate_maintenance_authorization(root, authorization, task=material.get("task"),
        sources=material.get("sources"), source_head=material.get("source_head"), evidence_only=True)
    return MAINTENANCE_SNAPSHOT_BYTES


def _validate_task_snapshot_budget(material: dict, *, root: Path | None = None) -> None:
    if len(serialize_json(material)) > task_snapshot_limit(root, material):
        raise ProposalError("task snapshot exceeds context budget")


def validate_task_snapshot_bytes(root: Path, raw: bytes) -> dict:
    """The field alone grants nothing; replay authority before the larger bound."""
    if type(raw) is not bytes or len(raw) > MAINTENANCE_SNAPSHOT_BYTES:
        raise ProposalError("task snapshot exceeds context budget")
    material = unique_json(raw.decode("utf-8"))
    if len(raw) > task_snapshot_limit(root, material):
        raise ProposalError("task snapshot exceeds context budget")
    _validate_task_snapshot_budget(material, root=root)
    return material


def read_task_snapshot(root: Path, path: Path, label: str = "task snapshot") -> tuple[bytes, dict]:
    """Bound before parse, then revalidate immutable maintenance authority or 480k."""
    raw = bounded_file_bytes(root, path, MAINTENANCE_SNAPSHOT_BYTES, label)
    return raw, validate_task_snapshot_bytes(root, raw)


def build_review_transport(
    candidate: dict,
    sources: dict,
    proposal_sha256: str,
) -> dict:
    if not isinstance(candidate, dict) or not isinstance(sources, dict):
        raise ProposalError("invalid review transport input")
    if not isinstance(proposal_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", proposal_sha256):
        raise ProposalError("invalid proposal SHA")
    changes = candidate.get("changes")
    if not isinstance(changes, list):
        raise ProposalError("invalid candidate changes")
    return {
        "canonical_proposal_sha256": proposal_sha256,
        "status": candidate["status"],
        "summary": candidate["summary"],
        "covered_requirements": list(candidate["covered_requirements"]),
        "findings": list(candidate["findings"]),
        "changed_paths": [change["path"] for change in changes],
        "unified_diff": proposal_diff(changes, sources),
    }


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(serialize_json(value))
    temporary.replace(path)


def _cli_stream_input(prompt: str, *, is_compact_review: bool = False) -> bytes:
    """Encode one unchanged logical prompt; wire expansion is a separate bound."""
    if type(prompt) is not str or not prompt or type(is_compact_review) is not bool:
        raise ProposalError("invalid CLI input prompt")
    logical_limit = 160_000 if is_compact_review else 2 * MAX_CONTEXT
    if len(prompt.encode("utf-8")) > logical_limit:
        raise ProposalError("CLI logical prompt exceeds budget")
    wire = (json.dumps({"event": "user", "message": {"content": prompt}}) + "\n").encode()
    # Default JSON string encoding expands each UTF8 input byte by at most six;
    # the fixed envelope plus final LF is46 bytes. No logical cap is enlarged.
    if len(wire) > 6 * logical_limit + 46:
        raise ProposalError("CLI input wire exceeds derived budget")
    return wire


_FRAMED_INPUT = "FULL_INPUT_ACK_V1"
_FRAMED_MANIFEST = "input-framing.json"


class _FramedQuotaError(ProposalError):
    """Existing strict quota evidence from a rejected real ERROR turn only."""

    def __init__(self, evidence: dict) -> None:
        super().__init__("provider quota observed; no replay authority")
        self.quota_evidence = dict(evidence)


def _raise_framed_terminal_error(events: list[dict], init: dict, start: int,
                                 model: str) -> None:
    """Classify an ERROR-only real turn, never a success/proof transcript."""
    turn = events[start:]
    if turn and turn[0] is init:
        turn = turn[1:]
    projection = "\n".join(json.dumps(e, ensure_ascii=False) for e in [init, *turn]) + "\n"
    try:
        # ERROR is already guarded by the caller, so business parsing is unreachable.
        _parse_stream(projection, {}, {}, "worker", expected_model=model)
    except ProposalError as error:
        quota = classify_gemini_quota_stream(projection, model, datetime.now(timezone.utc))
        if quota is not None:
            raise _FramedQuotaError(quota) from None
        if isinstance(error, RetryableProviderSessionError):
            # This path previously blocked; classification grants no new auto-retry.
            raise ProposalError(str(error)) from None
        raise
    raise ProposalError("framed ERROR unexpectedly passed legacy rejection")


def _input_ack(index: int, count: int, token: str) -> dict:
    # This exact transport-only object fits BOTH unchanged role schemas.
    return {"status": "BLOCKED", "summary": f"{_FRAMED_INPUT}:{index}/{count}:{token}",
            "covered_requirements": [], "findings": ["TRANSPORT_ACK_ONLY"], "changes": []}


def _framed_input_plan(prompt: str) -> tuple[dict, list[bytes]]:
    raw = prompt.encode("utf-8")
    if not MAX_CONTEXT < len(raw) <= 2 * MAX_CONTEXT:
        raise ProposalError("framed logical prompt size is invalid")
    chunks, offset = [], 0
    while offset < len(raw):
        end = min(offset + 118000, len(raw))
        while end < len(raw) and raw[end] & 0xC0 == 0x80:
            end -= 1
        chunks.append(raw[offset:end])
        offset = end
    if not 2 <= len(chunks) <= 3:
        raise ProposalError("framed input count is invalid")
    frames, wires, offset = [], [], 0
    for index, chunk in enumerate(chunks, 1):
        token = sha(_FRAMED_INPUT.encode() + str(index).encode() + chunk)[:24]
        ack = _input_ack(index, len(chunks), token) if index < len(chunks) else None
        instruction = (
            "Retain this exact fragment; do not solve or review the task yet. Return ONLY "
            "the existing five-key response object with status BLOCKED, empty covered_requirements "
            "and changes, findings [\"TRANSPORT_ACK_ONLY\"], and summary "
            f"\"{_FRAMED_INPUT}:{index}/{len(chunks)}:TOKEN\". Replace TOKEN with the "
            "END_FULL_INPUT_TOKEN after the complete fragment. This is a transport ACK, not a business result."
            if ack is not None else
            "FINAL fragment. Reassemble ALL preceding fragments and this fragment in exact order. "
            "They form ONE complete logical prompt, including ALL full source files. "
            "Now follow that whole prompt and return its final schema-bound business response; never a transport ACK."
        )
        text = (f"{_FRAMED_INPUT} transport fragment {index}/{len(chunks)}. " + instruction
                + "\nBEGIN_EXACT_LOGICAL_FRAGMENT\n" + chunk.decode("utf-8")
                + f"\nEND_EXACT_LOGICAL_FRAGMENT\nEND_FULL_INPUT_TOKEN={token}\n")
        if len(text.encode("utf-8")) > 120000:
            raise ProposalError("framed message exceeds 120000 bytes")
        wire = _cli_stream_input(text)
        frames.append({"index": index, "offset": offset, "utf8_bytes": len(chunk),
                       "sha256": sha(chunk), "wire_bytes": len(wire), "wire_sha256": sha(wire),
                       "ack": ack})
        wires.append(wire)
        offset += len(chunk)
    if sum(map(len, wires)) > 6 * (2 * MAX_CONTEXT) + 46:
        raise ProposalError("combined framed wire exceeds derived budget")
    return {"protocol": _FRAMED_INPUT, "logical_prompt_bytes": len(raw),
            "logical_prompt_sha256": sha(raw), "frame_count": len(frames), "frames": frames}, wires


def _framed_stream_projection(raw: str, manifest: dict, model: str, session: str,
                              *, complete: bool) -> tuple[int, str]:
    """Validate every real ACK/terminal; only then project the REAL final turn."""
    events = [unique_json(line) for line in raw.splitlines() if line.strip()]
    init, terminals, start = None, 0, 0
    for pos, event in enumerate(events):
        if terminals == manifest["frame_count"]:
            raise ProposalError("late event after framed final result")
        if type(event) is not dict or ("conversation_id" in event and event["conversation_id"] != session):
            raise ProposalError("framed event/session mismatch")
        kind = event.get("event")
        if kind in {"init", "result", "step_update"} and type(event.get(kind)) is not dict:
            raise ProposalError("malformed framed event envelope")
        step = event.get("step_update")
        if isinstance(step, dict) and "conversation_id" in step and step["conversation_id"] != session:
            raise ProposalError("framed step/session mismatch")
        if kind == "init":
            if (init is not None or type(event.get("init")) is not dict
                    or event["init"].get("model") != model or event.get("conversation_id") != session):
                raise ProposalError("framed init/model mismatch")
            init = event
        if kind in {"error", "denied", "permission_denied"}:
            raise ProposalError("error or denial invalidates framed stream")
        if terminals < manifest["frame_count"] - 1:
            for obj in (event, event.get("step_update")):
                if not isinstance(obj, dict):
                    continue
                if (obj.get("error") is not None or obj.get("denied_actions")
                        or obj.get("state") in {"ERROR", "DENIED"}):
                    raise ProposalError("error or denial invalidates framed ACK")
                if (any(k in obj for k in ("tool_name", "tool_info", "subagent_info"))
                        or obj.get("step_type") in {"tool", "subagent"}):
                    raise ProposalError("tool attempt invalidates framed ACK")
        if kind != "result":
            continue
        envelope = event.get("result")
        if (init is None or type(envelope) is not dict
                or envelope.get("conversation_id") != session or envelope.get("denied_actions")):
            raise ProposalError("framed terminal is not successful")
        terminals += 1
        if type(envelope.get("num_turns")) is not int or envelope["num_turns"] != terminals:
            raise ProposalError("framed cumulative turn count mismatch")
        if terminals > manifest["frame_count"]:
            raise ProposalError("extra framed terminal")
        if envelope.get("status") == "ERROR":
            # Keep the full raw transcript on disk; this projection is error-only.
            for item in events[start:pos + 1]:
                for obj in (item, item.get("step_update")):
                    if isinstance(obj, dict) and (
                            any(k in obj for k in ("tool_name", "tool_info", "subagent_info"))
                            or obj.get("step_type") in {"tool", "subagent"}
                            or obj.get("denied_actions")):
                        raise ProposalError("tool or denial invalidates framed ERROR")
            _raise_framed_terminal_error(events[:pos + 1], init, start, model)
        if envelope.get("status") != "SUCCESS":
            raise ProposalError("framed terminal is not successful")
        if terminals < manifest["frame_count"]:
            if "structured_output" in envelope:
                value = envelope["structured_output"]
            else:
                response = envelope.get("response")
                if type(response) is not str or len(response.encode("utf-8")) > 4096:
                    raise ProposalError("framed ACK response is invalid")
                value = unique_json(response)
            if value != manifest["frames"][terminals - 1]["ack"]:
                raise ProposalError("framed ACK mismatch")
            start = pos + 1
    if init is None or (complete and terminals != manifest["frame_count"]):
        raise ProposalError("incomplete framed stream")
    projection = "\n".join(json.dumps(e, ensure_ascii=False) for e in [init, *events[start:]]) + "\n"
    return terminals, projection


def _parse_framed_stream(raw: str, manifest: dict, task: dict, sources: dict, role: str,
                         force_full_paths: tuple[str, ...], model: str, session: str,
                         *, original_sources: dict | None = None) -> tuple[dict, dict]:
    _, final = _framed_stream_projection(raw, manifest, model, session, complete=True)
    result, envelope = _parse_stream(final, task, sources, role, force_full_paths, model,
                                     original_sources=original_sources)
    if result.get("summary", "").startswith(_FRAMED_INPUT + ":"):
        raise ProposalError("transport ACK cannot be the final result")
    return result, envelope


def _capture_framed_process(process: subprocess.Popen, wires: list[bytes], manifest: dict,
                            stdout_path: Path, stderr_path: Path, timeout: float,
                            model: str, session: str,
                            *, pre_provider_check: Callable[[], None] | None = None) -> None:
    """Bounded normal-exit-only exchange; each next write requires its real ACK."""
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    offsets, active, total, pending, accepted = [0] * len(wires), 0, 0, bytearray(), bytearray()
    primary = None
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            streams = {process.stdout: stdout, process.stderr: stderr}
            for stream in streams:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, "output")
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "input")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(getattr(process, "args", "CLI"), timeout)
                for key, _ in selector.select(min(remaining, .25)):
                    stream = key.fileobj
                    if key.data == "input":
                        try:
                            count = os.write(stream.fileno(), wires[active][offsets[active]:])
                        except BlockingIOError:
                            continue
                        if count <= 0:
                            raise ProposalError("framed input short write")
                        offsets[active] += count
                        if offsets[active] == len(wires[active]):
                            selector.unregister(stream)
                            if active == len(wires) - 1:
                                stream.close()
                        continue
                    try:
                        chunk = os.read(stream.fileno(), min(STREAM_CHUNK_SIZE, MAX_OUTPUT - total + 1))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    if total + len(chunk) > MAX_OUTPUT:
                        raise ProposalError("CLI output exceeds budget")
                    streams[stream].write(chunk)
                    total += len(chunk)
                    if stream is not process.stdout:
                        continue
                    pending.extend(chunk)
                    while b"\n" in pending:
                        line, _, rest = pending.partition(b"\n")
                        pending[:] = rest
                        accepted.extend(line + b"\n")
                        if not line.strip():
                            continue
                        event = unique_json(line.decode("utf-8"))
                        if type(event) is not dict:
                            raise ProposalError("framed event must be an object")
                        if event.get("event") == "result":
                            if offsets[active] != len(wires[active]):
                                raise ProposalError("framed terminal preceded its complete input")
                            count, _ = _framed_stream_projection(accepted.decode("utf-8"), manifest,
                                                                 model, session, complete=False)
                            if count != active + 1 or offsets[active] != len(wires[active]):
                                raise ProposalError("framed terminal preceded its complete input")
                            if active < len(wires) - 1:
                                if pre_provider_check is not None:
                                    pre_provider_check()
                                active += 1
                                selector.register(process.stdin, selectors.EVENT_WRITE, "input")
                if process.poll() is not None and not process.stdin.closed:
                    try:
                        selector.unregister(process.stdin)
                    except KeyError:
                        pass
                    process.stdin.close()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(getattr(process, "args", "CLI"), timeout)
            process.wait(timeout=remaining)
            if process.returncode != 0 or offsets != list(map(len, wires)) or pending.strip():
                raise ProposalError("framed exchange did not exit normally with complete input")
            _framed_stream_projection(accepted.decode("utf-8"), manifest, model, session, complete=True)
    except BaseException as exc:
        primary = exc
        try:
            terminate_process(process)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except BaseException:
            pass
        raise
    finally:
        cleanup = None
        for stream in (selector, process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None and (stream is selector or not stream.closed):
                    stream.close()
            except BaseException as exc:
                if cleanup is None:
                    cleanup = exc
        if primary is None and cleanup is not None:
            raise cleanup


def run(root: Path, task_path: Path, role: str, run_id: str, timeout: int,
        feedback: Path | None = None, proposal: Path | None = None,
        cli: Path | None = None, test_evidence: Path | None = None,
        *, pre_provider_check: Callable[[], None] | None = None,
        fallback_from_run_id: str | None = None,
        retry_hint: object = None, maintenance_authorization=None) -> Path:
    root = root.resolve(strict=True)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", run_id) or not 10 <= timeout <= 900:
        raise ProposalError("invalid run ID or timeout")
    if role not in {"worker", "verifier"}:
        raise ProposalError("invalid proposal role")
    autopilot_run_id = controller_run_id_from_environment(os.environ)
    # Contract/document drift must be rejected before task materialization and
    # before the Gemini CLI can consume a token or create a run artifact.
    runtime_contract_sha = _generation_contract_sha(root)
    model_config = configured_gemini_roles(root)
    selected_role = model_config["worker" if role == "worker" else "reviewer"]
    role_model = selected_role["model"]
    role_effort = selected_role["reasoning_effort"]
    task, sources = (load_task(root, task_path) if maintenance_authorization is None else
                     load_task(root, task_path, maintenance_authorization=maintenance_authorization))
    if maintenance_authorization is None:
        prep_notes = enrich_repository_dependency_context(
            root, task, sources, task_preparation_notes(task, sources)
        )
        validate_task_readiness(prep_notes)
    else:
        if role != "verifier":
            raise ProposalError("prepared maintenance author must use genuine Qoder author")
        # Closed immutable job loader has already validated complete Source,
        # selectors, identity, policy and320k envelope. No business admission waiver.
        prep_notes = {"maintenance_authorization": maintenance_authorization}
    head = git(root, "rev-parse", "HEAD")
    fallback_receipt = None
    if fallback_from_run_id is not None:
        if role != "worker":
            raise ProposalError("fallback is only supported for worker role")
        policy = load_worker_routing_policy(
            ROOT / "docs/refactor/two-server/templates/worker-model-routing.json", base_dir=ROOT,
        )
        if not policy.enabled:
            raise ProposalError("worker routing policy is disabled")
        if not isinstance(autopilot_run_id, str) or not autopilot_run_id.strip():
            raise ProposalError("nonempty controller ID required for fallback")
        if (
            not isinstance(fallback_from_run_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", fallback_from_run_id)
        ):
            raise ProposalError("invalid failed run ID")
        if run_id != f"{fallback_from_run_id}-gf" or len(run_id) > 80:
            raise ProposalError("fallback run ID must be exactly failed_id + '-gf' and at most 80 characters")
        fallback_receipt = _load_bound_opus_fallback_receipt(
            root, fallback_from_run_id, task, sources, head, autopilot_run_id
        )
    initial_status = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    material = {"task": task, "source_head": head, "sources": sources}
    if maintenance_authorization is not None:
        material["maintenance_authorization"] = maintenance_authorization
    input_hashes = {}
    for key, path in (("feedback", feedback), ("proposal", proposal), ("test_evidence", test_evidence)):
        if path is not None:
            maximum = (worker_result_limit(root, path.parent)
                       if key == "proposal" and maintenance_authorization is not None else MAX_CONTEXT)
            content = bounded_file_text(root, path, maximum, key)
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
    if test_evidence is not None and (role != "verifier" or proposal is None):
        raise ProposalError("test evidence is only allowed for verifier with a proposal")
    canonical_retry_hint = None
    if retry_hint is not None:
        if role != "worker" or feedback is not None or proposal is not None or fallback_from_run_id is not None:
            raise ProposalError("retry_hint is only supported for initial worker attempt")
        try:
            canonical_retry_hint = validate_retry_hint(retry_hint, task["allowed_files"])
        except ValueError as exc:
            raise ProposalError(str(exc)) from exc
    prior_status = None
    prompt_material = material
    review_transport_sha = None
    candidate_sources = None
    candidate_sources_sha256 = None
    force_full_paths: tuple[str, ...] = ()
    is_compact_review = False
    if proposal is not None:
        candidate, prior_status, proposal_sha256 = load_prior_worker(
            root, proposal, material["proposal"], task, sources, head
        )
        if input_hashes["proposal"] != proposal_sha256:
            raise ProposalError("proposal hash changed during validation")
        if role == "worker":
            material["feedback"] = validate_correction_feedback(
                material["feedback"], proposal_sha256
            )
            candidate_sources = build_candidate_sources(
                task["allowed_files"], sources, candidate["changes"]
            )
            candidate_sources_sha256 = sha(serialize_json(candidate_sources))
            force_full_paths = originally_new_paths(task, sources)
            prompt_material = {
                "task": task,
                "source_head": head,
                "sources": candidate_sources,
                "feedback": material["feedback"],
                "prior_identity": {
                    "run_id": prior_status["run_id"],
                    "proposal_sha256": proposal_sha256,
                },
                "correction_transport": {
                    "full_content_required_paths": list(force_full_paths),
                },
            }
        elif role == "verifier":
            if maintenance_authorization is None:
                review_proposal = build_review_transport(candidate, sources, proposal_sha256)
            else:
                raw_transport = unique_json(bounded_file_text(root, proposal.with_name("raw-result.json"), MAX_CONTEXT,
                                                            "prepared maintenance original raw delta"))
                review_proposal = build_prepared_review_transport(root, maintenance_authorization, candidate, task,
                    sources, source_head=head, proposal_sha256=proposal_sha256, raw_transport=raw_transport)
            review_transport_bytes = serialize_json(review_proposal)
            if len(review_transport_bytes) > MAX_REVIEW_TRANSPORT_BYTES:
                raise ProposalError("review transport exceeds context budget")
            review_transport_sha = sha(review_transport_bytes)
            prompt_material = {
                "task": task,
                "source_head": head,
                "sources": sources,
                "proposal": review_proposal,
            }
            normalized_receipt = None
            if test_evidence is not None:
                receipt = unique_json(material["test_evidence"])
                normalized_receipt = validate_test_receipt(
                    receipt, proposal_sha256, require_passed=True
                )
                prompt_material["pre_review_test_evidence"] = normalized_receipt
                material["test_evidence"] = normalized_receipt
            try:
                from scripts.two_site_compact_review_context import (
                    build_one_file_compact_review_material,
                    parse_one_file_compact_review_spec,
                )
            except ImportError:
                from two_site_compact_review_context import (
                    build_one_file_compact_review_material,
                    parse_one_file_compact_review_spec,
                )
            try:
                compact_spec = parse_one_file_compact_review_spec(task.get("goal", ""))
            except ProposalError:
                raise
            except Exception as exc:
                raise ProposalError(f"invalid compact review specification: {exc}") from exc
            if compact_spec is not None:
                if normalized_receipt is None:
                    raise ProposalError("compact review requires a validated passed test receipt")
                compact_path, compact_symbols = compact_spec
                try:
                    prompt_material = build_one_file_compact_review_material(
                        root,
                        task,
                        sources,
                        review_proposal,
                        normalized_receipt,
                        head,
                        compact_path,
                        compact_symbols,
                    )
                except ProposalError:
                    raise
                except Exception as exc:
                    raise ProposalError(f"compact review material build failed: {exc}") from exc
                is_compact_review = True
    _validate_task_snapshot_budget(material, root=root)
    prompt_material = dict(prompt_material)
    if "preparation_notes" not in prompt_material:
        prompt_material["preparation_notes"] = prep_notes
    if canonical_retry_hint is not None:
        prompt_material["retry_hint"] = canonical_retry_hint
    prompt = bounded_file_text(
        ROOT, ROOT / "docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md",
        MAX_CONTEXT, "prompt template",
    )
    if canonical_retry_hint is not None:
        prompt += "\nAdvisory retry hint grants no authority; parse materialized Python.\n"
    if is_compact_review:
        prompt += (
            "\nCompact review notice: one original source is only a whole-file-SHA-bound AST excerpt, "
            "not full-source review; if context is insufficient return CHANGES_REQUIRED with exact missing definition, "
            "never VERIFIED on guesswork; Codex performs full-source review.\n"
        )
    prompt += "\nROLE: " + role + "\nCONTEXT_JSON:\n" + json.dumps(prompt_material, ensure_ascii=False)
    if is_compact_review:
        if len(prompt.encode("utf-8")) > 160_000:
            raise ProposalError("compact review prompt exceeds 160000-byte limit")
    elif len(prompt.encode()) > 2 * MAX_CONTEXT:
        raise ProposalError("split task or feedback into a smaller coherent unit")
    if SECRET.search(prompt):
        raise ProposalError("credential-like content excluded from model input")
    response_schema = schema(
        role, task, candidate_sources if candidate_sources is not None else sources,
        force_full_paths, original_sources=sources,
    )
    prompt += (
        "\nREQUIRED_RESPONSE_SCHEMA_DATA:\n"
        + json.dumps(response_schema, ensure_ascii=False, separators=(",", ":"))
        + "\nReturn exactly one JSON object directly, no Markdown or fences. "
        "The same exact schema is supplied to the CLI; no tool, shell or file action is authorized.\n"
    )
    schema_prompt_limit = 160_000 if is_compact_review else 2 * MAX_CONTEXT
    if len(prompt.encode("utf-8")) > schema_prompt_limit:
        raise ProposalError("split task or feedback into a smaller coherent unit")
    stdin_bytes = _cli_stream_input(prompt, is_compact_review=is_compact_review)
    input_framing, framed_wires = None, None
    if not is_compact_review and len(prompt.encode("utf-8")) > MAX_CONTEXT:
        input_framing, framed_wires = _framed_input_plan(prompt)
        stdin_bytes = b"".join(framed_wires)
    out = None
    meta = None
    generation_process = None
    generation_policy = None
    capture_outcome = None
    completion_observations = None
    pending_delta = None
    first_primary = None
    try:
        with proposal_cli_context(root, cli) as (local, disposable_cli, cli_source):
            try:
                # The lock covers every CLI invocation, including identity probes.
                initial_cli_identity = cli_identity(disposable_cli)
                session_gate = derive_session_gate(prep_notes)
                out = local / run_id
                out.mkdir(mode=0o700)  # Evidence is never overwritten by a retry.
                write_json(out / "task.json", material)
                (out / "prompt.txt").write_text(prompt)
                meta = {"run_id": run_id, "task_id": task["task_id"], "role": role,
                        "head": head, "model": role_model, "effort": role_effort, "state": "RUNNING", "started_at": time.time(),
                        "retain_until": time.time() + 7 * 86400,
                        "authority": "PROPOSAL_ONLY_NOT_ACCEPTANCE",
                        "completion_policy": GENERATION_COMPLETION_POLICY,
                        "runtime_contract_sha256": runtime_contract_sha,
                        "model_config": model_config,
                        "model_config_sha256": sha(serialize_json(model_config)),
                        "compact_review": is_compact_review,
                        "cli": initial_cli_identity, "cli_source": cli_source,
                        "session_gate": session_gate}
                if input_framing is not None:
                    write_json(out / _FRAMED_MANIFEST, input_framing)
                    meta.update(input_transport=_FRAMED_INPUT,
                                input_framing_sha256=file_sha256(out / _FRAMED_MANIFEST))
                if autopilot_run_id is not None:
                    meta["controller_run_id"] = autopilot_run_id
                if prior_status is not None:
                    meta.update(
                        input_worker_run_id=prior_status["run_id"],
                        input_worker_conversation_id=prior_status["conversation_id"],
                        input_proposal_sha256=input_hashes["proposal"],
                    )
                if feedback is not None:
                    meta["input_feedback_sha256"] = input_hashes["feedback"]
                if test_evidence is not None:
                    meta["input_test_evidence_sha256"] = input_hashes["test_evidence"]
                if review_transport_sha is not None:
                    meta["review_transport_sha256"] = review_transport_sha
                if candidate_sources_sha256 is not None:
                    meta["candidate_sources_sha256"] = candidate_sources_sha256
                if fallback_from_run_id is not None:
                    meta["fallback_from_run_id"] = fallback_from_run_id
                    meta["fallback_receipt"] = fallback_receipt
                write_json(out / "status.json", meta)
                active_sources = candidate_sources if candidate_sources is not None else sources
                effort_args = ["--effort", role_effort]
                meta["generation_effort_requested"] = role_effort if effort_args else None
                meta["effort_arg_present"] = bool(effort_args)
                response_schema_path = out / "response-schema.json"
                write_json(response_schema_path, response_schema)
                response_schema_bytes = bounded_file_bytes(root, response_schema_path, MAX_CONTEXT, "response schema")
                if unique_json(response_schema_bytes.decode("utf-8")) != response_schema:
                    raise ProposalError("response schema binding changed before dispatch")
                meta["response_schema_sha256"] = sha(response_schema_bytes)
                write_json(out / "status.json", meta)
                command = [str(disposable_cli), "--mode", "plan", "--sandbox", "--model", role_model,
                           *effort_args, "--input-format", "stream-json",
                           "--output-format", "stream-json", "--print-timeout", f"{timeout}s",
                           "--json-schema", str(response_schema_path)]
                runtime_workspace = disposable_cli.parent / "proposal-runtime-workspace"
                runtime_workspace.mkdir(mode=0o700)
                (runtime_workspace / ".agents").mkdir(mode=0o700)
                write_json(runtime_workspace / ".agents/hooks.json", proposal_hooks())
                profile = prepare_profile(
                    disposable_cli, runtime_workspace, role_model,
                    role_effort if effort_args else None, terminate=terminate_process,
                )
                if cli_identity(disposable_cli) != initial_cli_identity:
                    raise ProposalError("CLI identity changed during profile preparation")
                if prior_status is not None and profile.conversation_id == prior_status["conversation_id"]:
                    raise ProposalError("profile preparation reused the worker session")
                if profile.model != role_model or profile.effort != meta["generation_effort_requested"]:
                    raise ProposalError("prepared role/profile model or requested effort changed")
                meta["prepared_effort"] = profile.effort
                meta["runtime_profile"] = validate_profile(profile, fresh=True)
                write_json(out / "status.json", meta)
                command += ["--conversation", profile.conversation_id]
                # Auth belongs to the CLI. No credential files are read or copied here.
                if pre_provider_check is not None:
                    pre_provider_check()
                if configured_gemini_roles(root) != model_config:
                    raise ProposalError("Gemini model configuration changed before dispatch")
                validate_profile(profile, fresh=True)
                if bounded_file_bytes(root, response_schema_path, MAX_CONTEXT, "response schema") != response_schema_bytes:
                    raise ProposalError("response schema binding changed before dispatch")
                def final_frame_check(stdout: bytes, stderr: bytes) -> bool:
                    return _generation_final_frame_check(
                        stdout, stderr, task, active_sources, role,
                        force_full_paths, role_model, profile.conversation_id,
                        original_sources=sources,
                    )

                if input_framing is None:
                    generation_policy = GenerationRecoveryPolicy(final_frame_check)
                capture_options = (
                    {"generation_completion": generation_policy}
                    if generation_policy is not None else {}
                )
                # Capture owns all pipes, bounded reap and failure cleanup. Do not
                # add Popen.__exit__'s unbounded wait after a failed cleanup.
                process = subprocess.Popen(
                    command, cwd=profile.workspace, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=True,
                    env=dict(os.environ, AGY_CLI_DISABLE_AUTO_UPDATE='true'),
                )
                generation_process = process
                meta["cli_pid"] = process.pid
                try:
                    write_json(out / "status.json", meta)
                    if input_framing is not None:
                        _capture_framed_process(
                            process, framed_wires, input_framing, out / "stream.jsonl",
                            out / "stderr.txt", timeout + 15, role_model, profile.conversation_id,
                            pre_provider_check=pre_provider_check,
                        )
                    else:
                        capture_outcome = capture_bounded_process(
                            process, stdin_bytes, out / "stream.jsonl", out / "stderr.txt",
                            timeout + 15, **capture_options,
                        )
                except BaseException as capture_error:
                    first_primary = capture_error
                    # A status-write failure can precede capture ownership.
                    # Bounded cleanup must not replace the original error.
                    try:
                        terminate_process(process)
                    except BaseException:
                        pass
                    for pipe in (process.stdin, process.stdout, process.stderr):
                        try:
                            if pipe is not None and not pipe.closed:
                                pipe.close()
                        except BaseException:
                            pass
                    if isinstance(capture_error, _FramedQuotaError):
                        if cli_identity(disposable_cli) != initial_cli_identity:
                            first_primary = ProposalError("CLI identity changed during generation")
                            raise first_primary from None
                        meta["quota_evidence"] = dict(capture_error.quota_evidence)
                    raise
                if capture_outcome is not None:
                    if (type(capture_outcome) is not dict
                            or capture_outcome.get("outcome") != "RECOVERED_COMPLETED_RESULT"
                            or capture_outcome.get("actual_returncode") != process.returncode):
                        raise ProposalError("generation capture returned an invalid recovery outcome")
                if capture_outcome is None and process.returncode:
                    raw_stream = bounded_file_text(root, out / "stream.jsonl", MAX_OUTPUT, "CLI event stream")
                    quota_evidence = classify_gemini_quota_stream(
                        raw_stream, role_model, datetime.now(timezone.utc)
                    )
                    if quota_evidence is not None:
                        final_cli_identity = cli_identity(disposable_cli)
                        if final_cli_identity != initial_cli_identity:
                            raise ProposalError("CLI identity changed during generation")
                        meta["quota_evidence"] = quota_evidence
                        write_json(out / "status.json", meta)
                    if is_retryable_zero_token_stream(raw_stream, role_model):
                        final_cli_identity = cli_identity(disposable_cli)
                        if final_cli_identity != initial_cli_identity:
                            raise ProposalError("CLI identity changed during generation")
                        raise RetryableProviderSessionError("CLI did not complete one successful session")
                    if is_retryable_interrupted_stream(raw_stream, role_model):
                        final_cli_identity = cli_identity(disposable_cli)
                        if final_cli_identity != initial_cli_identity:
                            raise ProposalError("CLI identity changed during generation")
                        raise RetryableProviderSessionError(
                            "provider stream interrupted before a complete result"
                        )
                    raise ProposalError(f"CLI failed with exit {process.returncode}; inspect run evidence")
                final_cli_identity = cli_identity(disposable_cli)
                if final_cli_identity != initial_cli_identity:
                    raise ProposalError("CLI identity changed during generation")
                if bounded_file_bytes(root, response_schema_path, MAX_CONTEXT, "response schema") != response_schema_bytes:
                    raise ProposalError("response schema binding changed during generation")
                raw = bounded_file_text(root, out / "stream.jsonl", MAX_OUTPUT, "CLI event stream")
                quota_evidence = classify_gemini_quota_stream(
                    raw, role_model, datetime.now(timezone.utc)
                )
                if quota_evidence is not None:
                    final_cli_identity = cli_identity(disposable_cli)
                    if final_cli_identity != initial_cli_identity:
                        raise ProposalError("CLI identity changed during generation")
                    meta["quota_evidence"] = quota_evidence
                    write_json(out / "status.json", meta)
                if input_framing is not None:
                    result, envelope = _parse_framed_stream(
                        raw, input_framing, task, active_sources, role, force_full_paths,
                        role_model, profile.conversation_id, original_sources=sources,
                    )
                elif capture_outcome is None:
                    result, envelope = _parse_stream(
                        raw, task, active_sources, role, force_full_paths, role_model,
                        original_sources=sources,
                    )
                else:
                    result, envelope = _validate_final_generation_frame(
                        raw, task, active_sources, role, force_full_paths, role_model,
                        profile.conversation_id, original_sources=sources,
                    )
                    if pre_provider_check is not None:
                        pre_provider_check()  # Late STOP/drift cannot grant next dispatch.
                capture_stdout = bounded_file_bytes(root, out / "stream.jsonl", MAX_OUTPUT,
                                                    "completed generation stdout")
                capture_stderr = bounded_file_bytes(root, out / "stderr.txt", MAX_OUTPUT,
                                                    "completed generation stderr")
                completion_observations = (
                    normal_observations(process.returncode, stdin_bytes, capture_stdout,
                                        capture_stderr, timeout + 15)
                    if capture_outcome is None else validate_observations(
                        capture_outcome, stdin_bytes, capture_stdout, capture_stderr,
                    )
                )
                if envelope["conversation_id"] != profile.conversation_id:
                    raise ProposalError("CLI result differs from prepared runtime session")
                if (prior_status is not None
                        and envelope["conversation_id"] == prior_status["conversation_id"]):
                    raise ProposalError("review/correction reused the worker session")
                # A late parent/source change invalidates the entire candidate.
                if head != git(root, "rev-parse", "HEAD") or initial_status != git(root, "status", "--porcelain=v1", "--untracked-files=all"):
                    raise ProposalError("Git state changed during generation")
                current_task, current_sources = (load_task(root, task_path) if maintenance_authorization is None else
            load_task(root, task_path, maintenance_authorization=maintenance_authorization))
                if current_task != task or current_sources != sources:
                    raise ProposalError("task or source bytes changed during generation")
                if fallback_from_run_id is not None:
                    rechecked_receipt = _load_bound_opus_fallback_receipt(
                        root,
                        fallback_from_run_id,
                        task,
                        sources,
                        head,
                        autopilot_run_id,
                    )
                    if rechecked_receipt != fallback_receipt:
                        raise ProposalError("Opus fallback receipt changed during execution")
                if candidate_sources is not None:
                    if result.get("status") != "PROPOSAL_READY":
                        raise ProposalError("correction delta proposal is not ready")
                    delta_result = result
                    try:
                        rebased = rebase_correction_result(
                            delta_result, task["allowed_files"], sources, candidate_sources
                        )
                    except CorrectionDeltaError as exc:
                        message = str(exc)
                        if (message.startswith("complete no-op rejection:")
                                or " in delta result" in message
                                or message.startswith("delta result ")):
                            raise RetryableProviderOutputError(
                                "provider returned unusable correction delta"
                            ) from exc
                        raise
                    rebased = validate_result(
                        rebased, task, sources, role, enforce_raw_transport=False,
                        original_sources=sources,
                    )
                    if len(serialize_json(rebased)) > MAX_CONTEXT:
                        raise ProposalError("canonical proposal exceeds the safe context budget")
                    pending_delta = delta_result
                    result = rebased
                # Supported profile/hooks must be checked while this workspace exists.
                # They cannot be read again after disposable context removal.
                meta["runtime_profile"] = validate_profile(profile, fresh=False)
                if completion_observations is None:
                    raise ProposalError("generation completion observations are missing")
                validate_runtime_contract(root)

            except BaseException as body_error:
                if first_primary is None:
                    first_primary = body_error
                raise
        # Actual normal context exit already verified the disposable exit pin,
        # removed its temporary tree and released the Runner lock.
        if head != git(root, "rev-parse", "HEAD") or initial_status != git(root, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ProposalError("Git state changed before generation publication")
        current_task, current_sources = (load_task(root, task_path) if maintenance_authorization is None else
            load_task(root, task_path, maintenance_authorization=maintenance_authorization))
        if current_task != task or current_sources != sources:
            raise ProposalError("task/source bytes changed before generation publication")
        if _generation_contract_sha(root) != runtime_contract_sha:
            raise ProposalError("generation contract changed before publication")
        if configured_gemini_roles(root) != model_config:
            raise ProposalError("Gemini model configuration changed before publication")
        if capture_outcome is not None and pre_provider_check is not None:
            pre_provider_check()
        if pending_delta is not None:
            write_json(out / "delta-result.json", pending_delta)
            meta["delta_result_sha256"] = file_sha256(out / "delta-result.json")
        write_json(out / "result.json", result)
        (out / "proposal.patch").write_text(proposal_diff(result["changes"], sources))
        meta.update(
            state=result["status"], conversation_id=envelope["conversation_id"],
            duration_seconds=round(time.time() - meta["started_at"], 3),
            usage=envelope.get("usage", {}), result_sha256=file_sha256(out / "result.json"),
            cli_returncode=generation_process.returncode,
            cli_normal_exit_observed=completion_observations["normal_exit_observed"],
            generation_completion_outcome=completion_observations["outcome"],
        )
        proof = _generation_artifact_bindings(
            root, out, task=task, sources=sources, role=role,
            status=meta, result=result, observations=completion_observations,
        )
        meta["completion_proof_sha256"] = _publish_generation_proof(
            out / _GENERATION_PROOF_NAME, proof,
        )
        # Status is the final eligibility publication and has no self-hash cycle.
        write_json(out / "status.json", meta)
        return out
    except BaseException as exit_error:
        primary = first_primary if first_primary is not None else exit_error
        if meta is not None and out is not None:
            meta.update(state="BLOCKED", error=type(primary).__name__ + ": " + str(primary),
                        duration_seconds=round(time.time() - meta["started_at"], 3))
            if primary is not exit_error:
                meta["secondary_context_error_type"] = type(exit_error).__name__
            if generation_policy is not None and generation_policy.failure_facts is not None:
                meta["generation_capture_failure"] = generation_policy.failure_facts
            if completion_observations is not None:
                meta["noneligible_completion_observations"] = completion_observations
            # These are actual post-close retained digests, distinct from the
            # observer's accepted-write domain in failure diagnostics.
            retained = {}
            for name in ("stream.jsonl", "stderr.txt"):
                try:
                    saved = bounded_file_bytes(root, out / name, MAX_OUTPUT,
                                               "failed generation retained output")
                    retained[name] = {"utf8_bytes": len(saved), "sha256": sha(saved)}
                except BaseException as read_error:
                    retained[name] = {"read_error_type": type(read_error).__name__}
            meta["failure_retained_outputs"] = retained
            try:
                write_json(out / "status.json", meta)
            except BaseException:
                pass
        if primary is exit_error:
            raise
        raise primary


def run_standalone(
    root: Path,
    task_path: Path,
    role: str,
    base_run_id: str,
    timeout: int,
    feedback: Path | None = None,
    proposal: Path | None = None,
    cli: Path | None = None,
    test_evidence: Path | None = None,
) -> Path:
    controller_run_id_from_environment(os.environ)
    proposals_dir = root / ".local/two-site-refactor/proposals"
    attempts = (
        base_run_id,
        f"{base_run_id}-retry-1",
        f"{base_run_id}-retry-2",
    )
    last_error: RetryableProviderSessionError | None = None
    output_errors = 0
    next_hint: dict[str, str | int] | None = None
    label = "reviewer" if role == "verifier" else "worker"
    for attempt_id in attempts:
        if (proposals_dir / attempt_id).exists():
            raise ProposalError(f"{label} run ID already exists: {attempt_id}")
        try:
            return run(
                root,
                task_path,
                role,
                attempt_id,
                timeout,
                feedback=feedback,
                proposal=proposal,
                cli=cli,
                test_evidence=test_evidence,
                **({"retry_hint": next_hint} if next_hint is not None else {}),
            )
        except RetryableProviderOutputError as exc:
            output_errors += 1
            last_error = exc
            if output_errors >= 2:
                raise ProposalError(
                    f"repeated provider output failure for {base_run_id} after 2 output attempts"
                ) from exc
            next_hint = None
            if (
                role == "worker"
                and feedback is None
                and proposal is None
                and isinstance(exc.__cause__, ProposalError)
                and str(exc.__cause__).startswith("generated Python syntax is invalid: ")
            ):
                task_data, _ = load_task(root, task_path)
                next_hint = extract_syntax_hint(
                    str(exc.__cause__), task_data["allowed_files"]
                )
        except RetryableProviderSessionError as exc:
            last_error = exc
            next_hint = None
    raise ProposalError(
        f"provider session retry exhausted for {base_run_id} after 3 attempts"
    ) from last_error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("worker", "verifier", "check"))
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--feedback", type=Path)
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--cli", type=Path, help="existing Antigravity executable (controller-owned)")
    parser.add_argument("--test-evidence", type=Path)
    args = parser.parse_args()
    if args.role in ("worker", "verifier") and args.run_id is None:
        parser.error("the following arguments are required: --run-id")
    try:
        if args.role == "check":
            if (args.run_id is not None or args.feedback is not None
                    or args.proposal is not None or args.cli is not None
                    or args.test_evidence is not None):
                raise ProposalError("check mode does not accept run-id, feedback, proposal, cli, or test-evidence arguments")
            payload = check(args.root, args.task)
            print(json.dumps(payload))
            return 0 if isinstance(payload, dict) and payload.get("state") == "TASK_READY" else 1
        out = run_standalone(
            args.root,
            args.task,
            args.role,
            args.run_id,
            args.timeout,
            feedback=args.feedback,
            proposal=args.proposal,
            cli=args.cli,
            test_evidence=args.test_evidence,
        )
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
