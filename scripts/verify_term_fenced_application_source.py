#!/usr/bin/env python3
"""Create or verify non-authorizing source evidence for a fenced app image.

This command is intentionally local and read-only except for its explicit
``build --output`` mode, which creates a new evidence file without replacing
anything.  It neither builds or loads an image, contacts a peer/Witness/Object
Storage, alters a Compose project, nor grants a writer term.

The eventual release process must still bind both app and bot image IDs and
repository digests in the existing signed release identity.  This tool closes
the smaller missing gap: the source claim in that identity has a repeatable,
machine-checkable proof that the startup code is term fenced.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import term_fenced_application_capability as capability  # noqa: E402


MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_BYTES = 64 * 1024
SAFE_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


class TermFencedApplicationSourceError(RuntimeError):
    """The checked Git tree cannot prove the fenced startup contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SourceTree:
    root: Path
    release_sha: str
    release_tree_sha: str
    blobs: dict[str, bytes]


def _fail(code: str) -> None:
    raise TermFencedApplicationSourceError(code)


def _closed_directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        _fail(f"TERM_FENCED_APPLICATION_SOURCE_{label}_INVALID")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise TermFencedApplicationSourceError(
            f"TERM_FENCED_APPLICATION_SOURCE_{label}_UNAVAILABLE"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        _fail(f"TERM_FENCED_APPLICATION_SOURCE_{label}_INVALID")
    return path


def _run_git(root: Path, *arguments: str, max_output: int = MAX_SOURCE_FILE_BYTES) -> bytes:
    try:
        completed = subprocess.run(
            ["/usr/bin/git", "-C", str(root), *arguments],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=SAFE_GIT_ENV,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TermFencedApplicationSourceError(
            "TERM_FENCED_APPLICATION_SOURCE_GIT_UNAVAILABLE"
        ) from exc
    if completed.returncode != 0 or len(completed.stdout) > max_output:
        _fail("TERM_FENCED_APPLICATION_SOURCE_GIT_REJECTED")
    return completed.stdout


def _git_single_line(root: Path, *arguments: str) -> str:
    try:
        text = _run_git(root, *arguments, max_output=4096).decode("ascii")
    except UnicodeDecodeError as exc:
        raise TermFencedApplicationSourceError(
            "TERM_FENCED_APPLICATION_SOURCE_GIT_REJECTED"
        ) from exc
    value = text.strip()
    if not value or "\n" in value:
        _fail("TERM_FENCED_APPLICATION_SOURCE_GIT_REJECTED")
    return value


def load_clean_source_tree(
    source_root: Path,
    *,
    expected_release_sha: str | None = None,
    expected_release_tree_sha: str | None = None,
) -> SourceTree:
    """Read only blobs from a clean checked-out Git tree, never worktree files."""

    root = _closed_directory(source_root, label="ROOT")
    release_sha = _git_single_line(root, "rev-parse", "--verify", "HEAD")
    release_tree_sha = _git_single_line(root, "rev-parse", "--verify", "HEAD^{tree}")
    if len(release_sha) != 40 or any(char not in "0123456789abcdef" for char in release_sha):
        _fail("TERM_FENCED_APPLICATION_SOURCE_RELEASE_INVALID")
    if len(release_tree_sha) != 40 or any(
        char not in "0123456789abcdef" for char in release_tree_sha
    ):
        _fail("TERM_FENCED_APPLICATION_SOURCE_TREE_INVALID")
    if expected_release_sha is not None and release_sha != expected_release_sha:
        _fail("TERM_FENCED_APPLICATION_SOURCE_RELEASE_MISMATCH")
    if expected_release_tree_sha is not None and release_tree_sha != expected_release_tree_sha:
        _fail("TERM_FENCED_APPLICATION_SOURCE_TREE_MISMATCH")
    if _run_git(root, "status", "--porcelain=v1", "--untracked-files=all", max_output=MAX_SOURCE_FILE_BYTES):
        _fail("TERM_FENCED_APPLICATION_SOURCE_WORKTREE_DIRTY")

    blobs: dict[str, bytes] = {}
    for relative in sorted(capability.TERM_FENCED_APPLICATION_CAPABILITY_FILES):
        blob = _run_git(root, "show", f"HEAD:{relative}", max_output=MAX_SOURCE_FILE_BYTES)
        if not blob:
            _fail("TERM_FENCED_APPLICATION_SOURCE_FILE_INVALID")
        blobs[relative] = blob
    return SourceTree(
        root=root,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        blobs=blobs,
    )


def _parse_python(blob: bytes, *, filename: str) -> ast.Module:
    try:
        source = blob.decode("utf-8")
        parsed = ast.parse(source, filename=filename)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise TermFencedApplicationSourceError(
            "TERM_FENCED_APPLICATION_SOURCE_PYTHON_INVALID"
        ) from exc
    if not isinstance(parsed, ast.Module):  # pragma: no cover - AST contract.
        _fail("TERM_FENCED_APPLICATION_SOURCE_PYTHON_INVALID")
    return parsed


def _functions(module: ast.Module, *, name: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]


def _single_function(module: ast.Module, *, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = _functions(module, name=name)
    if len(matches) != 1:
        _fail("TERM_FENCED_APPLICATION_SOURCE_STARTUP_FUNCTION_INVALID")
    return matches[0]


def _classes(module: ast.Module, *, name: str) -> list[ast.ClassDef]:
    return [node for node in module.body if isinstance(node, ast.ClassDef) and node.name == name]


def _single_method(
    module: ast.Module,
    *,
    class_name: str,
    method_name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    classes = _classes(module, name=class_name)
    if len(classes) != 1:
        _fail("TERM_FENCED_APPLICATION_SOURCE_MIDDLEWARE_INVALID")
    matches = [
        node
        for node in classes[0].body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    ]
    if len(matches) != 1:
        _fail("TERM_FENCED_APPLICATION_SOURCE_MIDDLEWARE_INVALID")
    return matches[0]


def _call_name(call: ast.Call) -> str | None:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    parts: list[str] = []
    while isinstance(function, ast.Attribute):
        parts.append(function.attr)
        function = function.value
    if isinstance(function, ast.Name):
        parts.append(function.id)
        return ".".join(reversed(parts))
    return None


def _calls(node: ast.AST) -> list[tuple[str, int, int, ast.Call]]:
    observed: list[tuple[str, int, int, ast.Call]] = []
    for item in ast.walk(node):
        if isinstance(item, ast.Call):
            name = _call_name(item)
            if name is not None:
                observed.append((name, item.lineno, item.col_offset, item))
    return sorted(observed, key=lambda entry: (entry[1], entry[2], entry[0]))


def _first_call_line(
    calls: Iterable[tuple[str, int, int, ast.Call]],
    *names: str,
) -> int | None:
    candidates = [line for name, line, _column, _call in calls if name in names]
    return min(candidates) if candidates else None


def _call_with_expected_service(
    calls: Iterable[tuple[str, int, int, ast.Call]],
    *,
    service: str,
) -> int | None:
    for name, line, _column, call in calls:
        if name != "validate_application_writer_term_runtime_settings":
            continue
        for keyword in call.keywords:
            if (
                keyword.arg == "expected_service"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value == service
            ):
                return line
    return None


def _require_before(
    first: int | None,
    later: Iterable[int | None],
    *,
    code: str,
) -> None:
    actual_later = [value for value in later if value is not None]
    if first is None or not actual_later or any(first >= value for value in actual_later):
        _fail(code)


def _settings_defaults(config_module: ast.Module) -> dict[str, object]:
    classes = _classes(config_module, name="Settings")
    if len(classes) != 1:
        _fail("TERM_FENCED_APPLICATION_SOURCE_CONFIG_INVALID")
    result: dict[str, object] = {}
    for statement in classes[0].body:
        if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
            continue
        if isinstance(statement.value, ast.Constant):
            result[statement.target.id] = statement.value.value
    return result


def _contains_call(node: ast.AST, *names: str) -> bool:
    return _first_call_line(_calls(node), *names) is not None


def _validate_runtime_config(
    config_module: ast.Module,
    term_module: ast.Module,
) -> None:
    defaults = _settings_defaults(config_module)
    expected = {
        "single_writer_runtime_enabled": False,
        "application_writer_term_enforced": False,
        "database_schema_bootstrap_enabled": True,
    }
    if any(defaults.get(name) is not value for name, value in expected.items()):
        _fail("TERM_FENCED_APPLICATION_SOURCE_CONFIG_INVALID")
    runtime_validation = _single_function(
        term_module,
        name="validate_application_writer_term_runtime",
    )
    calls = _calls(runtime_validation)
    if not _contains_call(runtime_validation, "policy_from_settings"):
        _fail("TERM_FENCED_APPLICATION_SOURCE_RUNTIME_POLICY_INVALID")
    source_literals = {
        item.value
        for item in ast.walk(runtime_validation)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    }
    if (
        "SINGLE_WRITER_RUNTIME_ENABLED must be true when Writer Witness enforcement is enabled"
        not in source_literals
        or "DATABASE_SCHEMA_BOOTSTRAP_ENABLED must be false when Writer Witness enforcement is enabled"
        not in source_literals
        or _call_with_expected_service(calls, service="api") is not None
    ):
        # The reusable policy function must validate a caller-supplied service
        # rather than accidentally hardcoding API-only behaviour.
        _fail("TERM_FENCED_APPLICATION_SOURCE_RUNTIME_POLICY_INVALID")
    # A name-level check is deliberate here: this source capability contract
    # requires the exact local Witness lease loader, not a weaker replacement.
    require_active = _single_function(term_module, name="require_active_writer_term")
    if not _contains_call(require_active, "load_production_writer_lease"):
        _fail("TERM_FENCED_APPLICATION_SOURCE_RUNTIME_POLICY_INVALID")


def _validate_database_gate(db_module: ast.Module) -> None:
    init_db = _single_function(db_module, name="init_db")
    calls = _calls(init_db)
    static_line = _first_call_line(calls, "validate_application_writer_term_runtime_settings")
    live_line = _first_call_line(calls, "require_application_writer_term")
    bootstrap_lines = [
        node.lineno
        for node in ast.walk(init_db)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and any(
            isinstance(item, ast.Attribute)
            and item.attr == "database_schema_bootstrap_enabled"
            for item in ast.walk(node.test)
        )
    ]
    ddl_line = _first_call_line(calls, "connection.run_sync")
    if (
        static_line is None
        or live_line is None
        or static_line >= live_line
        or not bootstrap_lines
        or live_line >= min(bootstrap_lines)
        or ddl_line is None
        or live_line >= ddl_line
    ):
        _fail("TERM_FENCED_APPLICATION_SOURCE_DATABASE_GATE_INVALID")
    for name in (
        "_enforce_application_writer_term_before_flush",
        "_enforce_application_writer_term_before_commit",
        "_enforce_application_writer_term_for_core_dml",
        "_enforce_application_writer_term_before_cursor_execute",
    ):
        if not _contains_call(_single_function(db_module, name=name), "require_application_writer_term"):
            _fail("TERM_FENCED_APPLICATION_SOURCE_DATABASE_GATE_INVALID")


def _validate_api_gate(main_module: ast.Module) -> None:
    startup = _single_function(main_module, name="_validate_writer_term_api_startup")
    startup_calls = _calls(startup)
    static_line = _call_with_expected_service(startup_calls, service="api")
    live_line = _first_call_line(startup_calls, "require_application_writer_term")
    if static_line is None or live_line is None or static_line >= live_line:
        _fail("TERM_FENCED_APPLICATION_SOURCE_API_GATE_INVALID")
    literals = {
        node.value
        for node in ast.walk(startup)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    if "BACKGROUND_JOBS_ENABLED must be false when Writer Witness enforcement is enabled" not in literals:
        _fail("TERM_FENCED_APPLICATION_SOURCE_API_GATE_INVALID")
    lifespan = _single_function(main_module, name="lifespan")
    calls = _calls(lifespan)
    gate_line = _first_call_line(calls, "_validate_writer_term_api_startup")
    _require_before(
        gate_line,
        (
            _first_call_line(calls, "configure_logging"),
            _first_call_line(calls, "init_db"),
            _first_call_line(calls, "init_redis"),
            _first_call_line(calls, "setup_event_listeners"),
        ),
        code="TERM_FENCED_APPLICATION_SOURCE_API_GATE_INVALID",
    )


def _validate_bot_gate(
    bot_module: ast.Module,
    middleware_module: ast.Module,
    readiness_module: ast.Module,
) -> None:
    main = _single_function(bot_module, name="main")
    if not isinstance(main, ast.AsyncFunctionDef):
        _fail("TERM_FENCED_APPLICATION_SOURCE_BOT_GATE_INVALID")
    calls = _calls(main)
    static_line = _call_with_expected_service(calls, service="bot")
    live_line = _first_call_line(calls, "require_application_writer_term")
    _require_before(
        static_line,
        (live_line,),
        code="TERM_FENCED_APPLICATION_SOURCE_BOT_GATE_INVALID",
    )
    _require_before(
        live_line,
        (
            _first_call_line(calls, "configure_logging"),
            _first_call_line(calls, "init_db"),
            _first_call_line(calls, "Bot"),
            _first_call_line(calls, "RedisStorage.from_url"),
            _first_call_line(calls, "Dispatcher"),
        ),
        code="TERM_FENCED_APPLICATION_SOURCE_BOT_GATE_INVALID",
    )
    request_fence_line = _first_call_line(calls, "_writer_term_request_middleware")
    update_fence_line = _first_call_line(calls, "WriterTermMiddleware")
    auth_line = _first_call_line(calls, "AuthMiddleware")
    if (
        request_fence_line is None
        or update_fence_line is None
        or auth_line is None
        or request_fence_line >= auth_line
        or update_fence_line >= auth_line
    ):
        _fail("TERM_FENCED_APPLICATION_SOURCE_BOT_GATE_INVALID")
    middleware_call = _single_method(
        middleware_module,
        class_name="WriterTermMiddleware",
        method_name="__call__",
    )
    if not _contains_call(middleware_call, "require_application_writer_term"):
        _fail("TERM_FENCED_APPLICATION_SOURCE_BOT_GATE_INVALID")
    readiness = _single_function(readiness_module, name="write_writer_ready_marker")
    if not _contains_call(readiness, "require_application_writer_term"):
        _fail("TERM_FENCED_APPLICATION_SOURCE_BOT_READINESS_INVALID")


def validate_source_capabilities(blobs: Mapping[str, bytes]) -> None:
    """Fail closed unless all exact startup and write-fence invariants exist."""

    if set(blobs) != capability.TERM_FENCED_APPLICATION_CAPABILITY_FILES:
        _fail("TERM_FENCED_APPLICATION_SOURCE_FILE_SET_INVALID")
    parsed = {name: _parse_python(blob, filename=name) for name, blob in blobs.items()}
    _validate_runtime_config(
        parsed["core/config.py"],
        parsed["core/application_writer_term.py"],
    )
    _validate_database_gate(parsed["core/db.py"])
    _validate_api_gate(parsed["main.py"])
    _validate_bot_gate(
        parsed["run_bot.py"],
        parsed["bot/middlewares/writer_term.py"],
        parsed["bot/writer_readiness.py"],
    )


def build_evidence(source_tree: SourceTree) -> bytes:
    """Build deterministic canonical evidence after semantic source checks."""

    validate_source_capabilities(source_tree.blobs)
    value: dict[str, Any] = {
        "schema": capability.TERM_FENCED_APPLICATION_CAPABILITY_SCHEMA,
        "status": capability.TERM_FENCED_APPLICATION_CAPABILITY_STATUS,
        "release_sha": source_tree.release_sha,
        "release_tree_sha": source_tree.release_tree_sha,
        "source_files": {
            name: hashlib.sha256(source_tree.blobs[name]).hexdigest()
            for name in sorted(capability.TERM_FENCED_APPLICATION_CAPABILITY_FILES)
        },
        "capabilities": list(capability.TERM_FENCED_APPLICATION_CAPABILITIES),
        "writer_authorized": False,
        "promotion_authorized": False,
        "deployment_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }
    document = capability.canonical_term_fenced_application_capability_json_bytes(value)
    capability.verify_term_fenced_application_capability(document)
    return document


def verify_evidence_for_source(
    source_tree: SourceTree,
    document: bytes,
) -> capability.TermFencedApplicationCapability:
    """Prove a supplied evidence document exactly matches this clean Git tree."""

    verified = capability.verify_term_fenced_application_capability(document)
    expected = build_evidence(source_tree)
    if document != expected:
        _fail("TERM_FENCED_APPLICATION_SOURCE_EVIDENCE_MISMATCH")
    if (
        verified.release_sha != source_tree.release_sha
        or verified.release_tree_sha != source_tree.release_tree_sha
    ):
        _fail("TERM_FENCED_APPLICATION_SOURCE_EVIDENCE_MISMATCH")
    return verified


def _secure_read_evidence(path: Path) -> bytes:
    if not path.is_absolute() or ".." in path.parts:
        _fail("TERM_FENCED_APPLICATION_SOURCE_EVIDENCE_PATH_INVALID")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size < 1
            or before.st_size > MAX_EVIDENCE_BYTES
        ):
            _fail("TERM_FENCED_APPLICATION_SOURCE_EVIDENCE_INVALID")
        payload = os.read(descriptor, MAX_EVIDENCE_BYTES + 1)
        after = os.fstat(descriptor)
        if len(payload) > MAX_EVIDENCE_BYTES or (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
            before.st_size,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
            after.st_size,
        ):
            _fail("TERM_FENCED_APPLICATION_SOURCE_EVIDENCE_INVALID")
        return payload
    except TermFencedApplicationSourceError:
        raise
    except OSError as exc:
        raise TermFencedApplicationSourceError(
            "TERM_FENCED_APPLICATION_SOURCE_EVIDENCE_UNAVAILABLE"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_new_evidence(path: Path, payload: bytes) -> None:
    if not path.is_absolute() or ".." in path.parts or not payload:
        _fail("TERM_FENCED_APPLICATION_SOURCE_OUTPUT_INVALID")
    try:
        parent = path.parent.lstat()
    except OSError as exc:
        raise TermFencedApplicationSourceError(
            "TERM_FENCED_APPLICATION_SOURCE_OUTPUT_UNAVAILABLE"
        ) from exc
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) & 0o022:
        _fail("TERM_FENCED_APPLICATION_SOURCE_OUTPUT_INVALID")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                _fail("TERM_FENCED_APPLICATION_SOURCE_OUTPUT_INVALID")
            offset += written
        os.fsync(descriptor)
    except TermFencedApplicationSourceError:
        raise
    except FileExistsError as exc:
        raise TermFencedApplicationSourceError(
            "TERM_FENCED_APPLICATION_SOURCE_OUTPUT_EXISTS"
        ) from exc
    except OSError as exc:
        raise TermFencedApplicationSourceError(
            "TERM_FENCED_APPLICATION_SOURCE_OUTPUT_UNAVAILABLE"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--expected-release-sha")
    parser.add_argument("--expected-release-tree-sha")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="create new canonical evidence from a clean Git tree")
    _source_arguments(build)
    build.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify", help="verify existing evidence against a clean Git tree")
    _source_arguments(verify)
    verify.add_argument("--evidence", required=True, type=Path)
    labels = commands.add_parser("image-labels", help="render required non-secret image labels")
    labels.add_argument("--evidence", required=True, type=Path)
    return parser


def _source_from_args(arguments: argparse.Namespace) -> SourceTree:
    return load_clean_source_tree(
        arguments.source_root,
        expected_release_sha=arguments.expected_release_sha,
        expected_release_tree_sha=arguments.expected_release_tree_sha,
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "build":
            source_tree = _source_from_args(arguments)
            document = build_evidence(source_tree)
            _write_new_evidence(arguments.output, document)
            result: Mapping[str, Any] = {
                "status": "ready",
                "kind": capability.TERM_FENCED_APPLICATION_CAPABILITY_STATUS,
                "release_sha": source_tree.release_sha,
                "release_tree_sha": source_tree.release_tree_sha,
                "evidence_sha256": hashlib.sha256(document).hexdigest(),
                "output": str(arguments.output),
            }
        elif arguments.command == "verify":
            source_tree = _source_from_args(arguments)
            document = _secure_read_evidence(arguments.evidence)
            verified = verify_evidence_for_source(source_tree, document)
            result = {
                "status": "ready",
                "kind": capability.TERM_FENCED_APPLICATION_CAPABILITY_STATUS,
                "release_sha": verified.release_sha,
                "release_tree_sha": verified.release_tree_sha,
                "evidence_sha256": verified.evidence_sha256,
            }
        else:
            verified = capability.verify_term_fenced_application_capability(
                _secure_read_evidence(arguments.evidence)
            )
            result = {
                "status": "ready",
                "kind": capability.TERM_FENCED_APPLICATION_CAPABILITY_STATUS,
                "release_sha": verified.release_sha,
                "release_tree_sha": verified.release_tree_sha,
                "evidence_sha256": verified.evidence_sha256,
                "labels": capability.expected_term_fenced_image_labels(verified),
            }
    except (TermFencedApplicationSourceError, capability.TermFencedApplicationCapabilityError) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
