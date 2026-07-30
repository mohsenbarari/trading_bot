#!/usr/bin/env python3
"""Create and verify one controller-local WebApp-FI source campaign binding.

The source transport has several immutable Object Storage routes, but their
campaign, application, and control pins must not come from an operator's
publish command.  This helper records those pins once in a root-only,
create-only canonical JSON file below an already-created campaign directory.

The binding is controller-local.  It contains no credential, recipient,
presigned URL, payload, or deployment state, and it performs no network,
Object Storage, SSH, Docker, or service action.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence


CAMPAIGN_BINDING_SCHEMA = "gold-trade-webapp-fi-source-campaign-binding-v1"
SOURCE_PHASE_DIRECTORY = "webapp-fi-source"
CAMPAIGN_BINDING_FILENAME = "campaign-binding.json"
MAXIMUM_BINDING_BYTES = 16 * 1024
GIT_BINARY = Path("/usr/bin/git")
GIT_TIMEOUT_SECONDS = 30
MIGRATION_DIRECTORY = PurePosixPath("migrations/versions")
MAXIMUM_MIGRATION_FILES = 4096
MAXIMUM_MIGRATION_SOURCE_BYTES = 1024 * 1024
MAXIMUM_MIGRATION_TREE_BYTES = 16 * 1024 * 1024

CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
GIT_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
ALEMBIC_REVISION_RE = re.compile(r"^[0-9a-f]{12}$")
MIGRATION_REVISION_RE = re.compile(r"^[A-Za-z0-9_]{1,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CampaignBindingError(RuntimeError):
    """A controller-local source campaign binding is unsafe or inconsistent."""


@dataclasses.dataclass(frozen=True)
class CampaignBinding:
    """Immutable source-phase pins consumed by controller transport commands."""

    campaign_id: str
    application_release_sha: str
    application_release_tree: str
    expected_alembic_revision: str
    control_commit: str
    control_tree: str
    binding_sha256: str


@dataclasses.dataclass(frozen=True)
class GitCheckoutIdentity:
    """One clean detached controller checkout verified without network access."""

    repository: Path
    commit: str
    tree: str


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Encode the persisted binding in the one canonical ASCII form."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CampaignBindingError("campaign binding JSON contains duplicate keys")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise CampaignBindingError(f"campaign binding JSON contains unsupported constant: {value}")


def _require_id(value: object, *, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise CampaignBindingError(f"{field} is invalid")
    return value


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise CampaignBindingError("controller source campaign binding operations must run as root")


def _require_absolute_path(value: Path, *, field: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        raise CampaignBindingError(f"{field} must be an absolute canonical path")
    return value


def _require_safe_ancestors(path: Path, *, field: str) -> None:
    """Reject symlinked or non-root-controlled ancestors.

    A root-owned sticky directory such as ``/tmp`` is accepted for isolated
    root-owned test roots.  The campaign directory and all newly-created
    source-phase components are stricter: exactly mode 0700.
    """

    path = _require_absolute_path(path, field=field)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            state = current.lstat()
        except OSError as exc:
            raise CampaignBindingError(f"{field} ancestor does not exist") from exc
        mode = stat.S_IMODE(state.st_mode)
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISDIR(state.st_mode)
            or state.st_uid != 0
            or (mode & 0o022 and not (state.st_mode & stat.S_ISVTX))
        ):
            raise CampaignBindingError(f"{field} has an unsafe ancestor")


def _require_root_private_directory(path: Path, *, field: str) -> Path:
    path = _require_absolute_path(path, field=field)
    _require_safe_ancestors(path.parent, field=field)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:
        raise CampaignBindingError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(state.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISDIR(target.st_mode)
        or target.st_uid != 0
        or stat.S_IMODE(target.st_mode) != 0o700
    ):
        raise CampaignBindingError(f"{field} must be one root-only mode 0700 non-symlink directory")
    return resolved


def _require_root_private_file(path: Path, *, field: str) -> Path:
    path = _require_absolute_path(path, field=field)
    _require_root_private_directory(path.parent, field=field + " parent")
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:
        raise CampaignBindingError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(state.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISREG(target.st_mode)
        or target.st_uid != 0
        or stat.S_IMODE(target.st_mode) != 0o600
        or target.st_nlink != 1
        or not 1 <= target.st_size <= MAXIMUM_BINDING_BYTES
    ):
        raise CampaignBindingError(f"{field} must be one bounded root-only mode 0600 regular non-symlink file")
    return resolved


def _require_root_controlled_directory(path: Path, *, field: str) -> Path:
    """Require one root-owned Git checkout or Git metadata directory.

    Checkouts may be readable by non-root so release tooling can inspect them,
    but neither the directory nor any lookup ancestor may be writable by a
    non-root account.
    """

    path = _require_absolute_path(path, field=field)
    _require_safe_ancestors(path.parent, field=field)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:
        raise CampaignBindingError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(state.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISDIR(target.st_mode)
        or target.st_uid != 0
        or stat.S_IMODE(target.st_mode) & 0o022
    ):
        raise CampaignBindingError(f"{field} must be one root-owned non-writable non-symlink directory")
    return resolved


def _require_root_controlled_executable(path: Path, *, field: str) -> Path:
    path = _require_absolute_path(path, field=field)
    _require_safe_ancestors(path.parent, field=field)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:
        raise CampaignBindingError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(state.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISREG(target.st_mode)
        or target.st_uid != 0
        or stat.S_IMODE(target.st_mode) & 0o022
        or not stat.S_IMODE(target.st_mode) & 0o100
    ):
        raise CampaignBindingError(f"{field} must be one root-owned non-writable executable non-symlink file")
    return resolved


def _read_root_private_file(path: Path, *, field: str) -> bytes:
    path = _require_root_private_file(path, field=field)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CampaignBindingError(f"cannot open {field}") from exc
    try:
        opened = os.fstat(descriptor)
        expected = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != expected.st_dev
            or opened.st_ino != expected.st_ino
            or opened.st_size != expected.st_size
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or not 1 <= opened.st_size <= MAXIMUM_BINDING_BYTES
        ):
            raise CampaignBindingError(f"{field} changed while being opened")
        chunks: list[bytes] = []
        remaining = MAXIMUM_BINDING_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != opened.st_size or len(payload) > MAXIMUM_BINDING_BYTES:
            raise CampaignBindingError(f"{field} changed while being read")
        return payload
    finally:
        os.close(descriptor)


def _binding_path_layout(path: Path) -> tuple[Path, Path]:
    """Return the campaign and source-phase directories for an exact binding path."""

    path = _require_absolute_path(path, field="campaign binding path")
    if path.name != CAMPAIGN_BINDING_FILENAME or path.parent.name != SOURCE_PHASE_DIRECTORY:
        raise CampaignBindingError("campaign binding path must be the canonical source-phase binding filename")
    campaign_directory = path.parent.parent
    if campaign_directory == path.parent or not CAMPAIGN_ID_RE.fullmatch(campaign_directory.name):
        raise CampaignBindingError("campaign binding path does not contain a valid campaign directory")
    return campaign_directory, path.parent


def campaign_binding_path(*, campaign_directory: Path, campaign_id: str) -> Path:
    """Return the only permitted binding filename below an existing campaign."""

    campaign_id = _require_id(campaign_id, field="campaign_id", pattern=CAMPAIGN_ID_RE)
    campaign_directory = _require_root_private_directory(campaign_directory, field="campaign directory")
    if campaign_directory.name != campaign_id:
        raise CampaignBindingError("campaign directory name does not match campaign_id")
    return campaign_directory / SOURCE_PHASE_DIRECTORY / CAMPAIGN_BINDING_FILENAME


def _create_or_require_source_phase_directory(campaign_directory: Path) -> Path:
    source_phase_directory = campaign_directory / SOURCE_PHASE_DIRECTORY
    created = False
    try:
        os.mkdir(source_phase_directory, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise CampaignBindingError("cannot create campaign source-phase directory") from exc
    else:
        created = True
    if created:
        try:
            os.chmod(source_phase_directory, 0o700)
        except OSError as exc:
            raise CampaignBindingError("cannot protect campaign source-phase directory") from exc
    return _require_root_private_directory(source_phase_directory, field="campaign source-phase directory")


def _fsync_root_private_directory(path: Path, *, field: str) -> None:
    path = _require_root_private_directory(path, field=field)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CampaignBindingError(f"cannot open {field}") from exc
    try:
        state = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(state.st_mode)
            or state.st_uid != 0
            or stat.S_IMODE(state.st_mode) != 0o700
        ):
            raise CampaignBindingError(f"{field} changed while being opened")
        os.fsync(descriptor)
    except CampaignBindingError:
        raise
    except OSError as exc:
        raise CampaignBindingError(f"cannot durably sync {field}") from exc
    finally:
        os.close(descriptor)


def _git_environment() -> dict[str, str]:
    """Return a minimal no-prompt environment for local Git inspection."""

    return {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "GIT_PAGER": "cat",
    }


def _run_git(repository: Path, arguments: Sequence[str], *, field: str) -> bytes:
    """Run one fixed local Git inspection command with no network operation."""

    _require_root_controlled_executable(GIT_BINARY, field="Git executable")
    if not all(isinstance(argument, str) and argument and "\x00" not in argument for argument in arguments):
        raise CampaignBindingError("Git inspection arguments are invalid")
    command = [
        str(GIT_BINARY),
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-C",
        str(repository),
        *arguments,
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_environment(),
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CampaignBindingError(f"cannot inspect {field} with local Git") from exc
    return result.stdout


def _git_single_line(payload: bytes, *, field: str, encoding: str = "ascii") -> str:
    if not isinstance(payload, bytes) or not payload.endswith(b"\n") or b"\n" in payload[:-1] or b"\r" in payload:
        raise CampaignBindingError(f"{field} is malformed")
    try:
        value = payload[:-1].decode(encoding)
    except UnicodeDecodeError as exc:
        raise CampaignBindingError(f"{field} is not valid {encoding}") from exc
    if not value or "\x00" in value:
        raise CampaignBindingError(f"{field} is malformed")
    return value


def _git_sha(payload: bytes, *, field: str) -> str:
    value = _git_single_line(payload, field=field)
    return _require_id(value, field=field, pattern=GIT_SHA_RE)


def _reported_git_directory(repository: Path, argument: str, *, field: str) -> Path:
    value = _git_single_line(
        _run_git(repository, ["rev-parse", "--path-format=absolute", argument], field=field),
        field=field,
        encoding="utf-8",
    )
    return _require_root_controlled_directory(Path(value), field=field)


def _verify_clean_detached_checkout(
    *,
    repository: Path,
    expected_commit: str,
    field: str,
) -> GitCheckoutIdentity:
    """Verify one exact clean detached Git checkout and derive its tree.

    The command set contains only local object/worktree inspection.  No fetch,
    checkout, reset, submodule, hook, credential, or network operation is
    performed here.
    """

    expected_commit = _require_id(expected_commit, field=f"{field} expected commit", pattern=GIT_SHA_RE)
    repository = _require_root_controlled_directory(repository, field=field)
    if _run_git(repository, ["rev-parse", "--is-inside-work-tree"], field=field) != b"true\n":
        raise CampaignBindingError(f"{field} is not a Git worktree")
    reported_top_level = _git_single_line(
        _run_git(repository, ["rev-parse", "--show-toplevel"], field=field),
        field=f"{field} top-level",
        encoding="utf-8",
    )
    if Path(reported_top_level) != repository:
        raise CampaignBindingError(f"{field} must be the exact Git worktree root")
    _reported_git_directory(repository, "--git-dir", field=f"{field} Git directory")
    _reported_git_directory(repository, "--git-common-dir", field=f"{field} common Git directory")
    if _git_single_line(
        _run_git(repository, ["rev-parse", "--abbrev-ref", "HEAD"], field=field),
        field=f"{field} HEAD reference",
    ) != "HEAD":
        raise CampaignBindingError(f"{field} must be detached at its expected commit")
    if _run_git(repository, ["status", "--porcelain=v1", "--untracked-files=all"], field=field):
        raise CampaignBindingError(f"{field} must be clean")
    head = _git_sha(_run_git(repository, ["rev-parse", "HEAD^{commit}"], field=field), field=f"{field} HEAD")
    if head != expected_commit:
        raise CampaignBindingError(f"{field} HEAD does not match its expected commit")
    verified = _git_sha(
        _run_git(repository, ["rev-parse", "--verify", expected_commit + "^{commit}"], field=field),
        field=f"{field} expected commit",
    )
    if verified != expected_commit:  # pragma: no cover - Git's verify output is independently checked.
        raise CampaignBindingError(f"{field} lacks its expected commit")
    tree = _git_sha(
        _run_git(repository, ["rev-parse", expected_commit + "^{tree}"], field=field),
        field=f"{field} tree",
    )
    # Recheck the mutable working-tree state after all object inspection.  A
    # changed checkout never reaches the create-only binding write.
    if _run_git(repository, ["status", "--porcelain=v1", "--untracked-files=all"], field=field):
        raise CampaignBindingError(f"{field} changed while being inspected")
    if _git_sha(
        _run_git(repository, ["rev-parse", "HEAD^{commit}"], field=field),
        field=f"{field} final HEAD",
    ) != expected_commit:
        raise CampaignBindingError(f"{field} changed while being inspected")
    if _git_sha(
        _run_git(repository, ["rev-parse", "HEAD^{tree}"], field=field),
        field=f"{field} final tree",
    ) != tree:
        raise CampaignBindingError(f"{field} changed while being inspected")
    return GitCheckoutIdentity(repository=repository, commit=expected_commit, tree=tree)


def _migration_reference_values(node: ast.AST, *, field: str) -> tuple[str, ...]:
    if isinstance(node, ast.Constant):
        if node.value is None:
            return ()
        if isinstance(node.value, str) and MIGRATION_REVISION_RE.fullmatch(node.value):
            return (node.value,)
    if isinstance(node, (ast.Tuple, ast.List)):
        values: list[str] = []
        for item in node.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str) or not MIGRATION_REVISION_RE.fullmatch(item.value):
                raise CampaignBindingError(f"{field} is invalid")
            values.append(item.value)
        if len(values) != len(set(values)):
            raise CampaignBindingError(f"{field} repeats a migration reference")
        return tuple(values)
    raise CampaignBindingError(f"{field} is invalid")


def _migration_assignments(payload: bytes, *, field: str) -> tuple[str, tuple[str, ...]]:
    if not 1 <= len(payload) <= MAXIMUM_MIGRATION_SOURCE_BYTES:
        raise CampaignBindingError(f"{field} has an unsafe size")
    try:
        source = payload.decode("utf-8")
        tree = ast.parse(source, filename=field, mode="exec")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise CampaignBindingError(f"{field} is not a valid UTF-8 Python migration") from exc
    values: dict[str, ast.AST] = {}
    for statement in tree.body:
        targets: list[ast.expr] = []
        value: ast.AST | None = None
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
            value = statement.value
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                if target.id in values:
                    raise CampaignBindingError(f"{field} assigns {target.id} more than once")
                values[target.id] = value
    if set(values) != {"revision", "down_revision"}:
        raise CampaignBindingError(f"{field} must assign revision and down_revision exactly once")
    revision_node = values["revision"]
    if (
        not isinstance(revision_node, ast.Constant)
        or not isinstance(revision_node.value, str)
        or not MIGRATION_REVISION_RE.fullmatch(revision_node.value)
    ):
        raise CampaignBindingError(f"{field} revision is invalid")
    return revision_node.value, _migration_reference_values(values["down_revision"], field=f"{field} down_revision")


def _derive_alembic_head(*, application: GitCheckoutIdentity) -> str:
    """Derive the sole Alembic head from literal migration metadata at HEAD."""

    raw = _run_git(
        application.repository,
        ["ls-tree", "-r", "-z", "--full-tree", application.commit, "--", MIGRATION_DIRECTORY.as_posix()],
        field="application migration tree",
    )
    if not raw or len(raw) > MAXIMUM_MIGRATION_TREE_BYTES:
        raise CampaignBindingError("application migration tree has an unsafe size")
    revisions: dict[str, tuple[str, ...]] = {}
    records = [record for record in raw.split(b"\x00") if record]
    if not records or len(records) > MAXIMUM_MIGRATION_FILES:
        raise CampaignBindingError("application migration tree has an unsafe file count")
    for record in records:
        try:
            header, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = header.decode("ascii").split(" ", 2)
            path_text = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise CampaignBindingError("application migration tree entry is malformed") from exc
        pure_path = PurePosixPath(path_text)
        if (
            pure_path.as_posix() != path_text
            or pure_path.parts[: len(MIGRATION_DIRECTORY.parts)] != MIGRATION_DIRECTORY.parts
            or len(pure_path.parts) != len(MIGRATION_DIRECTORY.parts) + 1
            or path_text.startswith("/")
            or ".." in pure_path.parts
            or mode != "100644"
            or object_type != "blob"
            or not GIT_SHA_RE.fullmatch(object_id)
        ):
            raise CampaignBindingError("application migration tree entry is invalid")
        if pure_path.name == "__init__.py":
            continue
        if not pure_path.name.endswith(".py"):
            # Alembic discovers Python migration modules only.  A tracked
            # helper/non-module file cannot create a revision edge, so omit it
            # rather than treating repository documentation or sentinels as a
            # second migration format.
            continue
        revision, down_revisions = _migration_assignments(
            _run_git(application.repository, ["cat-file", "blob", object_id], field="application migration blob"),
            field="application migration " + pure_path.name,
        )
        if revision in revisions:
            raise CampaignBindingError("application migration tree repeats a revision")
        revisions[revision] = down_revisions
    if not revisions:
        raise CampaignBindingError("application migration tree has no revision files")
    references = {reference for values in revisions.values() for reference in values}
    missing = references - set(revisions)
    if missing:
        raise CampaignBindingError("application migration tree has an unresolved revision reference")
    heads = sorted(set(revisions) - references)
    if len(heads) != 1 or not ALEMBIC_REVISION_RE.fullmatch(heads[0]):
        raise CampaignBindingError("application migration tree must have one supported Alembic head")
    return heads[0]


def _application_value(
    *,
    release_sha: object,
    release_tree: object,
    expected_alembic_revision: object,
) -> dict[str, str]:
    return {
        "release_sha": _require_id(release_sha, field="application.release_sha", pattern=GIT_SHA_RE),
        "release_tree": _require_id(release_tree, field="application.release_tree", pattern=GIT_SHA_RE),
        "expected_alembic_revision": _require_id(
            expected_alembic_revision,
            field="application.expected_alembic_revision",
            pattern=ALEMBIC_REVISION_RE,
        ),
    }


def _tooling_value(*, control_commit: object, control_tree: object) -> dict[str, str]:
    return {
        "control_commit": _require_id(control_commit, field="tooling.control_commit", pattern=GIT_SHA_RE),
        "control_tree": _require_id(control_tree, field="tooling.control_tree", pattern=GIT_SHA_RE),
    }


def _unsigned_binding(
    *,
    campaign_id: object,
    application_release_sha: object,
    application_release_tree: object,
    expected_alembic_revision: object,
    control_commit: object,
    control_tree: object,
) -> dict[str, Any]:
    return {
        "schema": CAMPAIGN_BINDING_SCHEMA,
        "status": "bound",
        "campaign_id": _require_id(campaign_id, field="campaign_id", pattern=CAMPAIGN_ID_RE),
        "application": _application_value(
            release_sha=application_release_sha,
            release_tree=application_release_tree,
            expected_alembic_revision=expected_alembic_revision,
        ),
        "tooling": _tooling_value(control_commit=control_commit, control_tree=control_tree),
    }


def build_campaign_binding(
    *,
    campaign_id: object,
    application_release_sha: object,
    application_release_tree: object,
    expected_alembic_revision: object,
    control_commit: object,
    control_tree: object,
) -> dict[str, Any]:
    """Build one validated in-memory binding before any file is created."""

    unsigned = _unsigned_binding(
        campaign_id=campaign_id,
        application_release_sha=application_release_sha,
        application_release_tree=application_release_tree,
        expected_alembic_revision=expected_alembic_revision,
        control_commit=control_commit,
        control_tree=control_tree,
    )
    return {**unsigned, "binding_sha256": sha256_bytes(canonical_json_bytes(unsigned))}


def _validate_binding_value(value: object) -> CampaignBinding:
    expected = {"schema", "status", "campaign_id", "application", "tooling", "binding_sha256"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise CampaignBindingError("campaign binding schema is unsupported")
    if value.get("schema") != CAMPAIGN_BINDING_SCHEMA or value.get("status") != "bound":
        raise CampaignBindingError("campaign binding schema is unsupported")
    application = value.get("application")
    tooling = value.get("tooling")
    if not isinstance(application, Mapping) or set(application) != {
        "release_sha",
        "release_tree",
        "expected_alembic_revision",
    }:
        raise CampaignBindingError("campaign binding application is invalid")
    if not isinstance(tooling, Mapping) or set(tooling) != {"control_commit", "control_tree"}:
        raise CampaignBindingError("campaign binding tooling is invalid")
    unsigned = _unsigned_binding(
        campaign_id=value.get("campaign_id"),
        application_release_sha=application.get("release_sha"),
        application_release_tree=application.get("release_tree"),
        expected_alembic_revision=application.get("expected_alembic_revision"),
        control_commit=tooling.get("control_commit"),
        control_tree=tooling.get("control_tree"),
    )
    binding_sha256 = _require_id(value.get("binding_sha256"), field="binding_sha256", pattern=SHA256_RE)
    if binding_sha256 != sha256_bytes(canonical_json_bytes(unsigned)):
        raise CampaignBindingError("campaign binding checksum is invalid")
    return CampaignBinding(
        campaign_id=unsigned["campaign_id"],
        application_release_sha=unsigned["application"]["release_sha"],
        application_release_tree=unsigned["application"]["release_tree"],
        expected_alembic_revision=unsigned["application"]["expected_alembic_revision"],
        control_commit=unsigned["tooling"]["control_commit"],
        control_tree=unsigned["tooling"]["control_tree"],
        binding_sha256=binding_sha256,
    )


def _parse_canonical_binding(payload: bytes) -> CampaignBinding:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAXIMUM_BINDING_BYTES:
        raise CampaignBindingError("campaign binding has an unsafe size")
    if b"://" in payload.lower() or b'"url"' in payload.lower() or b"presigned" in payload.lower():
        raise CampaignBindingError("campaign binding persists a forbidden URL")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CampaignBindingError("campaign binding is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise CampaignBindingError("campaign binding is not canonical JSON")
    return _validate_binding_value(value)


def create_campaign_binding(
    *,
    campaign_directory: Path,
    campaign_id: str,
    application_source_repository: Path,
    application_release_sha: str,
    expected_alembic_revision: str,
    control_source_repository: Path,
    control_commit: str,
) -> tuple[Path, CampaignBinding]:
    """Create one immutable binding from clean detached local Git checkouts.

    The two trees and the Alembic head are derived from the verified source
    checkouts.  Callers may name the expected release/control commits and the
    intended migration head, but cannot supply the resulting tree pins.
    """

    _require_root_execution()
    campaign_directory = _require_root_private_directory(campaign_directory, field="campaign directory")
    if campaign_directory.name != _require_id(campaign_id, field="campaign_id", pattern=CAMPAIGN_ID_RE):
        raise CampaignBindingError("campaign directory name does not match campaign_id")
    application = _verify_clean_detached_checkout(
        repository=Path(application_source_repository),
        expected_commit=application_release_sha,
        field="application source repository",
    )
    requested_revision = _require_id(
        expected_alembic_revision,
        field="expected_alembic_revision",
        pattern=ALEMBIC_REVISION_RE,
    )
    derived_revision = _derive_alembic_head(application=application)
    if derived_revision != requested_revision:
        raise CampaignBindingError("application migration head does not match expected_alembic_revision")
    control = _verify_clean_detached_checkout(
        repository=Path(control_source_repository),
        expected_commit=control_commit,
        field="control source repository",
    )
    final_application = _verify_clean_detached_checkout(
        repository=application.repository,
        expected_commit=application.commit,
        field="application source repository",
    )
    final_control = _verify_clean_detached_checkout(
        repository=control.repository,
        expected_commit=control.commit,
        field="control source repository",
    )
    if final_application != application or final_control != control:  # pragma: no cover - each verifier checks identity.
        raise CampaignBindingError("source checkout changed while campaign binding was being derived")
    value = build_campaign_binding(
        campaign_id=campaign_id,
        application_release_sha=application.commit,
        application_release_tree=application.tree,
        expected_alembic_revision=derived_revision,
        control_commit=control.commit,
        control_tree=control.tree,
    )
    expected_binding = _validate_binding_value(value)
    source_phase_directory = _create_or_require_source_phase_directory(campaign_directory)
    path = source_phase_directory / CAMPAIGN_BINDING_FILENAME
    encoded = canonical_json_bytes(value) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise CampaignBindingError("refusing to overwrite an existing campaign binding") from exc
    except OSError as exc:
        raise CampaignBindingError("cannot create campaign binding") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            pending = memoryview(encoded)
            while pending:
                written = handle.write(pending)
                if written is None:
                    written = len(pending)
                if written <= 0:  # pragma: no cover - regular file writes do not normally return zero.
                    raise OSError("short campaign binding write")
                pending = pending[written:]
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:
        # Preserve a failed create-only artifact as evidence.  This helper must
        # not remove a path it did not prove is exclusively its own.
        raise CampaignBindingError("cannot durably create campaign binding") from exc
    _fsync_root_private_directory(source_phase_directory, field="campaign source-phase directory")
    loaded = load_campaign_binding(path)
    if loaded != expected_binding:  # pragma: no cover - defensive invariant.
        raise CampaignBindingError("created campaign binding changed while being verified")
    return path, loaded


def load_campaign_binding(path: Path) -> CampaignBinding:
    """Load one exact canonical binding from its campaign source-phase path."""

    _require_root_execution()
    path = _require_absolute_path(path, field="campaign binding path")
    campaign_directory, source_phase_directory = _binding_path_layout(path)
    campaign_directory = _require_root_private_directory(campaign_directory, field="campaign directory")
    source_phase_directory = _require_root_private_directory(
        source_phase_directory,
        field="campaign source-phase directory",
    )
    if path.parent != source_phase_directory:
        raise CampaignBindingError("campaign binding path resolves outside its source-phase directory")
    binding = _parse_canonical_binding(_read_root_private_file(path, field="campaign binding"))
    if campaign_directory.name != binding.campaign_id:
        raise CampaignBindingError("campaign binding campaign_id does not match its directory")
    return binding


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create", help="create one immutable controller-local campaign binding")
    create.add_argument("--campaign-directory", required=True, type=Path)
    create.add_argument("--campaign-id", required=True)
    create.add_argument("--application-source-repository", required=True, type=Path)
    create.add_argument("--application-release-sha", required=True)
    create.add_argument("--expected-alembic-revision", required=True)
    create.add_argument("--control-source-repository", required=True, type=Path)
    create.add_argument("--control-commit", required=True)
    show = actions.add_parser("show", help="verify and print one immutable controller-local campaign binding")
    show.add_argument("--campaign-binding", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "create":
            path, binding = create_campaign_binding(
                campaign_directory=args.campaign_directory,
                campaign_id=args.campaign_id,
                application_source_repository=args.application_source_repository,
                application_release_sha=args.application_release_sha,
                expected_alembic_revision=args.expected_alembic_revision,
                control_source_repository=args.control_source_repository,
                control_commit=args.control_commit,
            )
            result: Mapping[str, Any] = {
                "status": "created",
                "campaign_binding": str(path),
                "campaign_id": binding.campaign_id,
                "binding_sha256": binding.binding_sha256,
            }
        else:
            binding = load_campaign_binding(args.campaign_binding)
            result = {
                "status": "verified",
                "campaign_id": binding.campaign_id,
                "application": {
                    "release_sha": binding.application_release_sha,
                    "release_tree": binding.application_release_tree,
                    "expected_alembic_revision": binding.expected_alembic_revision,
                },
                "tooling": {"control_commit": binding.control_commit, "control_tree": binding.control_tree},
                "binding_sha256": binding.binding_sha256,
            }
    except CampaignBindingError as exc:
        print(
            canonical_json_bytes(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}
            ).decode("ascii"),
            file=sys.stderr,
        )
        return 2
    print(canonical_json_bytes(result).decode("ascii"))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wrapper.
    raise SystemExit(main())
