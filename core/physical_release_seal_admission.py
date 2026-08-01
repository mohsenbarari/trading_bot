"""Fail-closed local admission for a fresh physical source/image release seal.

This module is intentionally narrower than release packaging.  It checks an
injected, root-controlled Git worktree observation and a caller-supplied set
of immutable image references, then mints an in-memory canonical descriptor.
It never executes Git itself, reads a source file, builds/pulls/pushes/loads
an image, talks to Docker, opens a network connection, contacts Object
Storage, opens SSH, or deploys anything.

The returned descriptor is a non-authorizing source/image provenance input for
the fresh WA-IR bootstrap bundle builder.  It is not an archive, an image
availability proof, a publish permit, a deployment permit, or a Full-Matrix
permit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol
from uuid import UUID

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    canonical_json_bytes,
)


__all__ = (
    "DEFAULT_PHYSICAL_RELEASE_SEAL_MAX_FRESHNESS_SECONDS",
    "FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY",
    "MAX_PHYSICAL_RELEASE_SEAL_MAX_FRESHNESS_SECONDS",
    "PHYSICAL_RELEASE_SEAL_ADMISSION_DEFAULT_ENABLED",
    "PHYSICAL_RELEASE_SEAL_ADMISSION_SCHEMA",
    "PHYSICAL_RELEASE_SEAL_DESCRIPTOR_SCHEMA",
    "PhysicalReleaseSealAdmissionConfig",
    "PhysicalReleaseSealAdmissionError",
    "PhysicalReleaseSealDescriptorProjection",
    "PhysicalReleaseSealFilesystemInspector",
    "PhysicalReleaseSealFilesystemObject",
    "PhysicalReleaseSealGitCommandResult",
    "PhysicalReleaseSealGitInvocation",
    "PhysicalReleaseSealGitRunner",
    "PhysicalReleaseSealImage",
    "PhysicalReleaseSealWorktreeInspection",
    "REQUIRED_PHYSICAL_RELEASE_IMAGE_ROLES",
    "SealedPhysicalReleaseDescriptor",
    "admit_physical_release_seal",
    "parse_physical_release_seal_descriptor",
    "project_physical_release_seal_for_wa_ir_bootstrap",
    "require_sealed_physical_release_descriptor",
)


PHYSICAL_RELEASE_SEAL_ADMISSION_SCHEMA = (
    "gold-trade-physical-release-seal-admission-v1"
)
PHYSICAL_RELEASE_SEAL_DESCRIPTOR_SCHEMA = (
    "gold-trade-physical-release-seal-descriptor-v1"
)
PHYSICAL_RELEASE_SEAL_ADMISSION_DEFAULT_ENABLED = False

DEFAULT_PHYSICAL_RELEASE_SEAL_MAX_FRESHNESS_SECONDS = 180
MAX_PHYSICAL_RELEASE_SEAL_MAX_FRESHNESS_SECONDS = 300
MAX_PHYSICAL_RELEASE_SEAL_FUTURE_SKEW_SECONDS = 5
MAX_PHYSICAL_RELEASE_SEAL_GIT_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_PHYSICAL_RELEASE_SEAL_TREE_ENTRIES = 100_000

FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY = Path("/usr/bin/git")

# A complete physical release must name every independently consumed role even
# when several roles deliberately resolve to the same immutable image digest.
REQUIRED_PHYSICAL_RELEASE_IMAGE_ROLES = (
    "webapp_fi_app",
    "webapp_ir_app",
    "bot_fi",
    "postgres_15",
    "redis_7",
    "witness",
)

_SOURCE_SITE = "webapp_fi"
_DESTINATION_SITE = "webapp_ir"
_STATUS = "sealed-source-image-descriptor-local-only"
_TREE_SCHEMA = "gold-trade-physical-release-seal-tracked-tree-v1"
_SOURCE_BUNDLE_SCHEMA = "gold-trade-physical-release-seal-source-tree-bundle-v1"
_PROVENANCE_SCHEMA = "gold-trade-physical-release-seal-provenance-v1"
_DESCRIPTOR_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "release_sha",
        "control_release_sha",
        "git_tree_id",
        "tracked_tree_sha256",
        "release_bundle_sha256",
        "images",
        "image_set_sha256",
        "release_provenance_sha256",
        "seal_id",
        "sealed_at",
        "source_site",
        "destination_site",
        "direct_fi_to_ir_control",
        "publish_authorized",
        "deployment_authorized",
        "execution_authorized",
        "descriptor_sha256",
    }
)
_IMAGE_FIELDS = frozenset({"role", "reference"})
_COMMIT_OR_TREE_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$", re.ASCII)
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$", re.ASCII)
_TREE_MODE_RE = re.compile(r"^(?:100644|100755)$", re.ASCII)
_TREE_OBJECT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$", re.ASCII)
_TREE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._+@=-]{1,255}$", re.ASCII)
_IMAGE_REF_RE = re.compile(
    r"^[a-z0-9][a-z0-9._/:-]{1,511}@sha256:[0-9a-f]{64}$", re.ASCII
)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)
_MUTABLE_IMAGE_COMPONENTS = frozenset({"alias", "current", "head", "latest", "pointer"})
_GIT_ENVIRONMENT = (
    # The injected local runner receives this complete environment rather
    # than inheriting the controller process environment.  The command scope
    # entries deliberately override any repository-local alias, fsmonitor,
    # hook, maintenance, pager, or submodule setting before Git dispatches a
    # command.  This module still does not execute Git; the concrete local
    # adapter validates this exact tuple before it can do so.
    ("GIT_CONFIG_NOSYSTEM", "1"),
    ("GIT_CONFIG_SYSTEM", "/dev/null"),
    ("GIT_CONFIG_GLOBAL", "/dev/null"),
    ("GIT_CONFIG_COUNT", "10"),
    ("GIT_CONFIG_KEY_0", "alias.rev-parse"),
    ("GIT_CONFIG_VALUE_0", ""),
    ("GIT_CONFIG_KEY_1", "alias.status"),
    ("GIT_CONFIG_VALUE_1", ""),
    ("GIT_CONFIG_KEY_2", "alias.ls-tree"),
    ("GIT_CONFIG_VALUE_2", ""),
    ("GIT_CONFIG_KEY_3", "core.fsmonitor"),
    ("GIT_CONFIG_VALUE_3", "false"),
    ("GIT_CONFIG_KEY_4", "core.useBuiltinFSMonitor"),
    ("GIT_CONFIG_VALUE_4", "false"),
    ("GIT_CONFIG_KEY_5", "core.hooksPath"),
    ("GIT_CONFIG_VALUE_5", "/dev/null"),
    ("GIT_CONFIG_KEY_6", "maintenance.auto"),
    ("GIT_CONFIG_VALUE_6", "false"),
    ("GIT_CONFIG_KEY_7", "pager.status"),
    ("GIT_CONFIG_VALUE_7", "false"),
    ("GIT_CONFIG_KEY_8", "submodule.recurse"),
    ("GIT_CONFIG_VALUE_8", "false"),
    ("GIT_CONFIG_KEY_9", "status.submoduleSummary"),
    ("GIT_CONFIG_VALUE_9", "false"),
    ("GIT_OPTIONAL_LOCKS", "0"),
    # No allowed command has a transport phase.  Deny every protocol as a
    # second containment layer should a future Git configuration regress.
    ("GIT_ALLOW_PROTOCOL", ""),
    ("GIT_PROTOCOL_FROM_USER", "0"),
    ("GIT_PAGER", "cat"),
    ("GIT_TERMINAL_PROMPT", "0"),
    ("GIT_EXEC_PATH", "/nonexistent"),
    ("HOME", "/nonexistent"),
    ("LC_ALL", "C"),
    ("PATH", "/usr/bin:/bin"),
    ("TZ", "UTC"),
)

_SEALED_DESCRIPTOR_CAPABILITY = object()


class PhysicalReleaseSealAdmissionError(ValueError):
    """A fixed-code refusal from the physical release-seal admission gate."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalReleaseSealImage:
    """One explicit role-to-immutable-image mapping; no image operation occurs."""

    role: str
    reference: str


@dataclass(frozen=True)
class PhysicalReleaseSealFilesystemObject:
    """Bounded local metadata returned by the injected filesystem inspector."""

    path: Path
    owner_uid: int
    mode: int
    regular_file: bool
    directory: bool
    symlink: bool
    executable: bool
    ancestors_root_controlled: bool
    # Identity and immutable metadata are intentionally part of the
    # observation.  Admission compares the pre- and post-Git observations,
    # so a path replacement or metadata mutation during inspection fails
    # closed instead of looking like the same root-owned path twice.
    device: int
    inode: int
    ctime_ns: int
    mtime_ns: int


@dataclass(frozen=True)
class PhysicalReleaseSealWorktreeInspection:
    """Root-controlled worktree, Git metadata, and fixed Git binary facts."""

    worktree: PhysicalReleaseSealFilesystemObject
    git_metadata: PhysicalReleaseSealFilesystemObject
    git_binary: PhysicalReleaseSealFilesystemObject


class PhysicalReleaseSealFilesystemInspector(Protocol):
    """Injected local metadata collector; it must not mutate the worktree."""

    def inspect_worktree(
        self,
        *,
        worktree: Path,
    ) -> PhysicalReleaseSealWorktreeInspection:
        """Return bounded facts for the fixed worktree and Git executable."""


@dataclass(frozen=True)
class PhysicalReleaseSealGitInvocation:
    """One exact local Git read-only invocation supplied to an injected runner."""

    executable: Path
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    worktree: Path


@dataclass(frozen=True)
class PhysicalReleaseSealGitCommandResult:
    """Only exit status and stdout are accepted from the injected Git runner."""

    exit_code: int
    stdout_bytes: bytes


class PhysicalReleaseSealGitRunner(Protocol):
    """Injected local-only Git command runner; no remote Git operations exist."""

    def run(
        self,
        *,
        invocation: PhysicalReleaseSealGitInvocation,
    ) -> PhysicalReleaseSealGitCommandResult:
        """Run exactly the immutable inspection invocation."""


@dataclass(frozen=True)
class PhysicalReleaseSealAdmissionConfig:
    """Default-off, root-only source/image seal input with no secret fields."""

    worktree: Path | None = None
    campaign_id: str = ""
    expected_release_sha: str = ""
    images: tuple[PhysicalReleaseSealImage, ...] = ()
    seal_id: UUID | None = None
    sealed_at: datetime | None = None
    enabled: bool = PHYSICAL_RELEASE_SEAL_ADMISSION_DEFAULT_ENABLED
    maximum_freshness_seconds: int = DEFAULT_PHYSICAL_RELEASE_SEAL_MAX_FRESHNESS_SECONDS


@dataclass(frozen=True)
class PhysicalReleaseSealDescriptorProjection:
    """Public source/image provenance fields; explicitly not deployment authority."""

    campaign_id: str
    release_sha: str
    control_release_sha: str
    git_tree_id: str
    tracked_tree_sha256: str
    release_bundle_sha256: str
    images: tuple[PhysicalReleaseSealImage, ...]
    image_set_sha256: str
    release_provenance_sha256: str
    seal_id: UUID
    sealed_at: datetime
    descriptor_sha256: str


@dataclass(frozen=True)
class SealedPhysicalReleaseDescriptor:
    """Opaque canonical source/image descriptor; never a publish/deploy permit."""

    canonical_descriptor: bytes
    descriptor_sha256: str
    campaign_id: str
    release_sha: str
    control_release_sha: str
    git_tree_id: str
    tracked_tree_sha256: str
    release_bundle_sha256: str
    image_set_sha256: str
    release_provenance_sha256: str
    seal_id: UUID
    sealed_at: datetime
    publish_authorized: bool = False
    deployment_authorized: bool = False
    execution_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _ConfigFacts:
    worktree: Path
    campaign_id: str
    release_sha: str
    images: tuple[PhysicalReleaseSealImage, ...]
    seal_id: UUID
    sealed_at: datetime
    maximum_freshness_seconds: int


@dataclass(frozen=True)
class _DescriptorFacts:
    projection: PhysicalReleaseSealDescriptorProjection
    canonical_descriptor: bytes


def _fail(code: str) -> None:
    raise PhysicalReleaseSealAdmissionError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError):
        _fail(code)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _release_sha(value: object, *, code: str) -> str:
    if type(value) is not str or RELEASE_SHA_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _campaign_id(value: object, *, code: str) -> str:
    if type(value) is not str or CAMPAIGN_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _uuid(value: object, *, code: str) -> UUID:
    if type(value) is not UUID or value.int == 0:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    normalized = parsed.astimezone(timezone.utc)
    if _timestamp_text(normalized) != value:
        _fail(code)
    return normalized


def _safe_worktree(value: object, *, code: str) -> Path:
    if (
        not isinstance(value, Path)
        or not value.is_absolute()
        or value == Path("/")
        or any(part in {"", ".", ".."} for part in value.parts[1:])
    ):
        _fail(code)
    return value


def _image_reference(value: object, *, code: str) -> str:
    if type(value) is not str or _IMAGE_REF_RE.fullmatch(value) is None:
        _fail(code)
    name, digest = value.split("@sha256:", 1)
    if not name or not digest or digest == "0" * 64 or "@" in name:
        _fail(code)
    parts = name.split("/")
    if any(
        not part
        or part in {".", ".."}
        or part.lower() in _MUTABLE_IMAGE_COMPONENTS
        for part in parts
    ):
        _fail(code)
    # A colon in an image-path component is a mutable Docker tag.  A registry
    # port is allowed only in the first component, only when a repository path
    # follows it, and only as one bounded decimal port.
    if any(":" in part for part in parts[1:]) or (
        ":" in parts[0] and len(parts) == 1
    ):
        _fail(code)
    if ":" in parts[0]:
        host, separator, port = parts[0].partition(":")
        if (
            not separator
            or not host
            or not port.isascii()
            or not port.isdecimal()
            or not 1 <= len(port) <= 5
        ):
            _fail(code)
    return value


def _normalise_images(value: object, *, code: str) -> tuple[PhysicalReleaseSealImage, ...]:
    if not isinstance(value, tuple) or len(value) != len(REQUIRED_PHYSICAL_RELEASE_IMAGE_ROLES):
        _fail(code)
    by_role: dict[str, PhysicalReleaseSealImage] = {}
    for item in value:
        if type(item) is not PhysicalReleaseSealImage:
            _fail(code)
        if type(item.role) is not str or _ROLE_RE.fullmatch(item.role) is None:
            _fail(code)
        if item.role in by_role:
            _fail(code)
        by_role[item.role] = PhysicalReleaseSealImage(
            role=item.role,
            reference=_image_reference(item.reference, code=code),
        )
    if set(by_role) != set(REQUIRED_PHYSICAL_RELEASE_IMAGE_ROLES):
        _fail(code)
    return tuple(by_role[role] for role in REQUIRED_PHYSICAL_RELEASE_IMAGE_ROLES)


def _fresh_seal(
    sealed_at: object,
    *,
    now: datetime,
    maximum_freshness_seconds: int,
    code: str,
) -> datetime:
    value = _utc(sealed_at, code=code)
    if value > now + timedelta(seconds=MAX_PHYSICAL_RELEASE_SEAL_FUTURE_SKEW_SECONDS):
        _fail(code)
    if value < now - timedelta(seconds=maximum_freshness_seconds):
        _fail(code)
    return value


def _normalise_config(
    value: object,
    *,
    now: datetime,
) -> _ConfigFacts:
    if type(value) is not PhysicalReleaseSealAdmissionConfig:
        _fail("PHYSICAL_RELEASE_SEAL_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("PHYSICAL_RELEASE_SEAL_DISABLED")
    maximum = value.maximum_freshness_seconds
    if (
        type(maximum) is not int
        or not 1 <= maximum <= MAX_PHYSICAL_RELEASE_SEAL_MAX_FRESHNESS_SECONDS
    ):
        _fail("PHYSICAL_RELEASE_SEAL_CONFIG_INVALID")
    return _ConfigFacts(
        worktree=_safe_worktree(value.worktree, code="PHYSICAL_RELEASE_SEAL_WORKTREE_INVALID"),
        campaign_id=_campaign_id(value.campaign_id, code="PHYSICAL_RELEASE_SEAL_CONFIG_INVALID"),
        release_sha=_release_sha(
            value.expected_release_sha, code="PHYSICAL_RELEASE_SEAL_CONFIG_INVALID"
        ),
        images=_normalise_images(value.images, code="PHYSICAL_RELEASE_SEAL_IMAGE_SET_INVALID"),
        seal_id=_uuid(value.seal_id, code="PHYSICAL_RELEASE_SEAL_CONFIG_INVALID"),
        sealed_at=_fresh_seal(
            value.sealed_at,
            now=now,
            maximum_freshness_seconds=maximum,
            code="PHYSICAL_RELEASE_SEAL_STALE",
        ),
        maximum_freshness_seconds=maximum,
    )


def _validate_filesystem_object(
    value: object,
    *,
    expected_path: Path,
    kind: str,
) -> PhysicalReleaseSealFilesystemObject:
    if type(value) is not PhysicalReleaseSealFilesystemObject:
        _fail("PHYSICAL_RELEASE_SEAL_FILESYSTEM_EVIDENCE_INVALID")
    if value.path != expected_path or not isinstance(value.path, Path):
        _fail("PHYSICAL_RELEASE_SEAL_FILESYSTEM_PATH_MISMATCH")
    if (
        type(value.owner_uid) is not int
        or value.owner_uid != 0
        or type(value.mode) is not int
        or not 0 <= value.mode <= 0o777
        or value.symlink is not False
        or value.ancestors_root_controlled is not True
        or value.mode & 0o022
        or type(value.device) is not int
        or value.device < 0
        or type(value.inode) is not int
        or value.inode < 1
        or type(value.ctime_ns) is not int
        or value.ctime_ns < 0
        or type(value.mtime_ns) is not int
        or value.mtime_ns < 0
    ):
        _fail("PHYSICAL_RELEASE_SEAL_FILESYSTEM_OWNERSHIP_OR_MODE_INVALID")
    if kind == "directory":
        if (
            value.directory is not True
            or value.regular_file is not False
            or value.mode & 0o500 != 0o500
        ):
            _fail("PHYSICAL_RELEASE_SEAL_FILESYSTEM_EVIDENCE_INVALID")
    elif kind == "executable":
        if (
            value.regular_file is not True
            or value.directory is not False
            or value.executable is not True
            or not value.mode & 0o100
        ):
            _fail("PHYSICAL_RELEASE_SEAL_FILESYSTEM_EVIDENCE_INVALID")
    else:  # pragma: no cover - internal fixed call sites
        _fail("PHYSICAL_RELEASE_SEAL_FILESYSTEM_EVIDENCE_INVALID")
    return value


def _inspect_worktree(
    *,
    inspector: PhysicalReleaseSealFilesystemInspector,
    worktree: Path,
) -> PhysicalReleaseSealWorktreeInspection:
    try:
        observed = inspector.inspect_worktree(worktree=worktree)
    except Exception as exc:  # pragma: no cover - OS-specific adapter failure
        raise PhysicalReleaseSealAdmissionError(
            "PHYSICAL_RELEASE_SEAL_FILESYSTEM_EVIDENCE_UNAVAILABLE"
        ) from exc
    if type(observed) is not PhysicalReleaseSealWorktreeInspection:
        _fail("PHYSICAL_RELEASE_SEAL_FILESYSTEM_EVIDENCE_INVALID")
    _validate_filesystem_object(
        observed.worktree, expected_path=worktree, kind="directory"
    )
    _validate_filesystem_object(
        observed.git_metadata,
        expected_path=worktree / ".git",
        kind="directory",
    )
    _validate_filesystem_object(
        observed.git_binary,
        expected_path=FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY,
        kind="executable",
    )
    return observed


def _git_invocation(worktree: Path, *arguments: str) -> PhysicalReleaseSealGitInvocation:
    argv = (str(FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY), "-C", str(worktree), *arguments)
    return PhysicalReleaseSealGitInvocation(
        executable=FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY,
        arguments=argv,
        environment=_GIT_ENVIRONMENT,
        worktree=worktree,
    )


def _run_git(
    *,
    runner: PhysicalReleaseSealGitRunner,
    invocation: PhysicalReleaseSealGitInvocation,
    code: str,
) -> bytes:
    try:
        result = runner.run(invocation=invocation)
    except Exception as exc:  # pragma: no cover - runner failures vary by OS
        raise PhysicalReleaseSealAdmissionError(code) from exc
    if (
        type(result) is not PhysicalReleaseSealGitCommandResult
        or type(result.exit_code) is not int
        or result.exit_code != 0
        or type(result.stdout_bytes) is not bytes
        or len(result.stdout_bytes) > MAX_PHYSICAL_RELEASE_SEAL_GIT_OUTPUT_BYTES
    ):
        _fail(code)
    return result.stdout_bytes


def _git_exact_commit(
    *,
    runner: PhysicalReleaseSealGitRunner,
    worktree: Path,
    target: str,
    code: str,
) -> str:
    raw = _run_git(
        runner=runner,
        invocation=_git_invocation(worktree, "rev-parse", "--verify", target),
        code=code,
    )
    try:
        text = raw.decode("ascii", "strict")
    except UnicodeDecodeError:
        _fail(code)
    if not _COMMIT_OR_TREE_RE.fullmatch(text.removesuffix("\n")) or text != text.removesuffix("\n") + "\n":
        _fail(code)
    return text[:-1]


def _require_clean_status(
    *,
    runner: PhysicalReleaseSealGitRunner,
    worktree: Path,
) -> None:
    raw = _run_git(
        runner=runner,
        invocation=_git_invocation(
            worktree,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignored=matching",
        ),
        code="PHYSICAL_RELEASE_SEAL_GIT_STATUS_FAILED",
    )
    # This includes ordinary dirty paths, all untracked paths, and ignored
    # paths.  A source seal has no ignored-file exception.
    if raw:
        _fail("PHYSICAL_RELEASE_SEAL_DIRTY_OR_UNTRACKED_WORKTREE")


def _tree_path(value: bytes) -> str:
    try:
        path = value.decode("ascii", "strict")
    except UnicodeDecodeError:
        _fail("PHYSICAL_RELEASE_SEAL_TRACKED_TREE_NONCANONICAL")
    if not path or path.startswith("/") or path.endswith("/") or "//" in path:
        _fail("PHYSICAL_RELEASE_SEAL_TRACKED_TREE_NONCANONICAL")
    components = path.split("/")
    if any(
        component in {"", ".", "..", ".git"}
        or _TREE_COMPONENT_RE.fullmatch(component) is None
        for component in components
    ):
        _fail("PHYSICAL_RELEASE_SEAL_TRACKED_TREE_NONCANONICAL")
    return path


def _tracked_tree_sha256(raw: bytes) -> str:
    if not raw or not raw.endswith(b"\0"):
        _fail("PHYSICAL_RELEASE_SEAL_TRACKED_TREE_NONCANONICAL")
    records = raw[:-1].split(b"\0")
    if not 1 <= len(records) <= MAX_PHYSICAL_RELEASE_SEAL_TREE_ENTRIES:
        _fail("PHYSICAL_RELEASE_SEAL_TRACKED_TREE_NONCANONICAL")
    entries: list[dict[str, str]] = []
    paths: set[str] = set()
    for record in records:
        try:
            header, path_raw = record.split(b"\t", 1)
            mode_raw, kind_raw, object_raw = header.split(b" ", 2)
            mode = mode_raw.decode("ascii", "strict")
            kind = kind_raw.decode("ascii", "strict")
            object_id = object_raw.decode("ascii", "strict")
        except (UnicodeDecodeError, ValueError):
            _fail("PHYSICAL_RELEASE_SEAL_TRACKED_TREE_NONCANONICAL")
        path = _tree_path(path_raw)
        if (
            _TREE_MODE_RE.fullmatch(mode) is None
            or kind != "blob"
            or _TREE_OBJECT_RE.fullmatch(object_id) is None
            or path in paths
        ):
            _fail("PHYSICAL_RELEASE_SEAL_TRACKED_TREE_NONCANONICAL")
        paths.add(path)
        entries.append({"mode": mode, "object_id": object_id, "path": path})
    entries.sort(key=lambda entry: entry["path"])
    return hashlib.sha256(
        _canonical(
            {"schema": _TREE_SCHEMA, "entries": entries},
            code="PHYSICAL_RELEASE_SEAL_TRACKED_TREE_NONCANONICAL",
        )
    ).hexdigest()


def _image_mappings(images: tuple[PhysicalReleaseSealImage, ...]) -> list[dict[str, str]]:
    return [{"role": image.role, "reference": image.reference} for image in images]


def _image_set_sha256(images: tuple[PhysicalReleaseSealImage, ...]) -> str:
    return hashlib.sha256(
        _canonical(_image_mappings(images), code="PHYSICAL_RELEASE_SEAL_IMAGE_SET_INVALID")
    ).hexdigest()


def _release_bundle_sha256(
    *,
    release_sha: str,
    git_tree_id: str,
    tracked_tree_sha256: str,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "schema": _SOURCE_BUNDLE_SCHEMA,
                "release_sha": release_sha,
                "git_tree_id": git_tree_id,
                "tracked_tree_sha256": tracked_tree_sha256,
            },
            code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID",
        )
    ).hexdigest()


def _release_provenance_sha256(
    *,
    campaign_id: str,
    release_sha: str,
    git_tree_id: str,
    tracked_tree_sha256: str,
    release_bundle_sha256: str,
    images: tuple[PhysicalReleaseSealImage, ...],
    image_set_sha256: str,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "schema": _PROVENANCE_SCHEMA,
                "campaign_id": campaign_id,
                "release_sha": release_sha,
                "control_release_sha": release_sha,
                "git_tree_id": git_tree_id,
                "tracked_tree_sha256": tracked_tree_sha256,
                "release_bundle_sha256": release_bundle_sha256,
                "images": _image_mappings(images),
                "image_set_sha256": image_set_sha256,
                "source_site": _SOURCE_SITE,
                "destination_site": _DESTINATION_SITE,
            },
            code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID",
        )
    ).hexdigest()


def _descriptor_unsigned_mapping(
    projection: PhysicalReleaseSealDescriptorProjection,
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_RELEASE_SEAL_DESCRIPTOR_SCHEMA,
        "status": _STATUS,
        "campaign_id": projection.campaign_id,
        "release_sha": projection.release_sha,
        "control_release_sha": projection.control_release_sha,
        "git_tree_id": projection.git_tree_id,
        "tracked_tree_sha256": projection.tracked_tree_sha256,
        "release_bundle_sha256": projection.release_bundle_sha256,
        "images": _image_mappings(projection.images),
        "image_set_sha256": projection.image_set_sha256,
        "release_provenance_sha256": projection.release_provenance_sha256,
        "seal_id": str(projection.seal_id),
        "sealed_at": _timestamp_text(projection.sealed_at),
        "source_site": _SOURCE_SITE,
        "destination_site": _DESTINATION_SITE,
        "direct_fi_to_ir_control": False,
        "publish_authorized": False,
        "deployment_authorized": False,
        "execution_authorized": False,
    }


def _descriptor_bytes(
    projection: PhysicalReleaseSealDescriptorProjection,
) -> tuple[bytes, str]:
    unsigned = _descriptor_unsigned_mapping(projection)
    descriptor_sha256 = hashlib.sha256(
        _canonical(unsigned, code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    ).hexdigest()
    return (
        _canonical(
            {**unsigned, "descriptor_sha256": descriptor_sha256},
            code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID",
        )
        + b"\n",
        descriptor_sha256,
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_NONCANONICAL")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_NONCANONICAL")


def _parse_images(value: object) -> tuple[PhysicalReleaseSealImage, ...]:
    if not isinstance(value, list) or len(value) != len(REQUIRED_PHYSICAL_RELEASE_IMAGE_ROLES):
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    images: list[PhysicalReleaseSealImage] = []
    for item in value:
        if type(item) is not dict or set(item) != _IMAGE_FIELDS:
            _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
        images.append(
            PhysicalReleaseSealImage(
                role=item["role"],
                reference=item["reference"],
            )
        )
    result = _normalise_images(
        tuple(images), code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID"
    )
    if _image_mappings(result) != value:
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_NONCANONICAL")
    return result


def _parse_descriptor(raw: object) -> _DescriptorFacts:
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_PHYSICAL_RELEASE_SEAL_GIT_OUTPUT_BYTES:
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_NONCANONICAL")
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_NONCANONICAL")
    if type(value) is not dict or set(value) != _DESCRIPTOR_FIELDS:
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    canonical = _canonical(value, code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_NONCANONICAL") + b"\n"
    if raw != canonical:
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_NONCANONICAL")
    if (
        value["schema"] != PHYSICAL_RELEASE_SEAL_DESCRIPTOR_SCHEMA
        or value["status"] != _STATUS
        or value["source_site"] != _SOURCE_SITE
        or value["destination_site"] != _DESTINATION_SITE
        or value["direct_fi_to_ir_control"] is not False
        or value["publish_authorized"] is not False
        or value["deployment_authorized"] is not False
        or value["execution_authorized"] is not False
    ):
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    campaign_id = _campaign_id(value["campaign_id"], code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    release_sha = _release_sha(value["release_sha"], code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    control_release_sha = _release_sha(
        value["control_release_sha"], code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID"
    )
    if control_release_sha != release_sha:
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    git_tree_id = value["git_tree_id"]
    if type(git_tree_id) is not str or _COMMIT_OR_TREE_RE.fullmatch(git_tree_id) is None:
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    tracked_tree_sha256 = _sha256(
        value["tracked_tree_sha256"], code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID"
    )
    images = _parse_images(value["images"])
    image_set_sha256 = _sha256(
        value["image_set_sha256"], code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID"
    )
    if image_set_sha256 != _image_set_sha256(images):
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    release_bundle_sha256 = _sha256(
        value["release_bundle_sha256"], code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID"
    )
    if release_bundle_sha256 != _release_bundle_sha256(
        release_sha=release_sha,
        git_tree_id=git_tree_id,
        tracked_tree_sha256=tracked_tree_sha256,
    ):
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    release_provenance_sha256 = _sha256(
        value["release_provenance_sha256"], code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID"
    )
    if release_provenance_sha256 != _release_provenance_sha256(
        campaign_id=campaign_id,
        release_sha=release_sha,
        git_tree_id=git_tree_id,
        tracked_tree_sha256=tracked_tree_sha256,
        release_bundle_sha256=release_bundle_sha256,
        images=images,
        image_set_sha256=image_set_sha256,
    ):
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    seal_id_raw = value["seal_id"]
    if type(seal_id_raw) is not str:
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    try:
        seal_id = UUID(seal_id_raw)
    except (TypeError, ValueError, AttributeError):
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    if seal_id.int == 0 or str(seal_id) != seal_id_raw:
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    sealed_at = _timestamp(value["sealed_at"], code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    projection = PhysicalReleaseSealDescriptorProjection(
        campaign_id=campaign_id,
        release_sha=release_sha,
        control_release_sha=control_release_sha,
        git_tree_id=git_tree_id,
        tracked_tree_sha256=tracked_tree_sha256,
        release_bundle_sha256=release_bundle_sha256,
        images=images,
        image_set_sha256=image_set_sha256,
        release_provenance_sha256=release_provenance_sha256,
        seal_id=seal_id,
        sealed_at=sealed_at,
        descriptor_sha256=_sha256(
            value["descriptor_sha256"], code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID"
        ),
    )
    expected_raw, expected_sha256 = _descriptor_bytes(projection)
    if (
        projection.descriptor_sha256 != expected_sha256
        or raw != expected_raw
    ):
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    return _DescriptorFacts(projection=projection, canonical_descriptor=expected_raw)


def parse_physical_release_seal_descriptor(
    raw: bytes,
) -> PhysicalReleaseSealDescriptorProjection:
    """Parse a canonical, non-authorizing descriptor without external I/O."""

    return _parse_descriptor(raw).projection


def admit_physical_release_seal(
    *,
    config: PhysicalReleaseSealAdmissionConfig,
    filesystem_inspector: PhysicalReleaseSealFilesystemInspector,
    git_runner: PhysicalReleaseSealGitRunner,
    now: datetime,
) -> SealedPhysicalReleaseDescriptor:
    """Admit exactly one clean root-owned source/image identity in memory.

    The function invokes only injected, fixed read-only Git inspection
    requests.  It has no package, image, network, or deployment capability.
    """

    assessed_at = _utc(now, code="PHYSICAL_RELEASE_SEAL_CLOCK_INVALID")
    facts = _normalise_config(config, now=assessed_at)
    if os.geteuid() != 0:
        _fail("PHYSICAL_RELEASE_SEAL_ROOT_RUNTIME_REQUIRED")
    pre_inspection = _inspect_worktree(
        inspector=filesystem_inspector, worktree=facts.worktree
    )
    head_before = _git_exact_commit(
        runner=git_runner,
        worktree=facts.worktree,
        target="HEAD^{commit}",
        code="PHYSICAL_RELEASE_SEAL_GIT_HEAD_FAILED",
    )
    if head_before != facts.release_sha:
        _fail("PHYSICAL_RELEASE_SEAL_HEAD_RELEASE_MISMATCH")
    _require_clean_status(runner=git_runner, worktree=facts.worktree)
    git_tree_id = _git_exact_commit(
        runner=git_runner,
        worktree=facts.worktree,
        target=f"{facts.release_sha}^{{tree}}",
        code="PHYSICAL_RELEASE_SEAL_GIT_TREE_FAILED",
    )
    tracked_tree = _run_git(
        runner=git_runner,
        invocation=_git_invocation(
            facts.worktree,
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            facts.release_sha,
        ),
        code="PHYSICAL_RELEASE_SEAL_GIT_TREE_FAILED",
    )
    tracked_tree_sha256 = _tracked_tree_sha256(tracked_tree)
    _require_clean_status(runner=git_runner, worktree=facts.worktree)
    head_after = _git_exact_commit(
        runner=git_runner,
        worktree=facts.worktree,
        target="HEAD^{commit}",
        code="PHYSICAL_RELEASE_SEAL_GIT_HEAD_FAILED",
    )
    if head_after != facts.release_sha:
        _fail("PHYSICAL_RELEASE_SEAL_UNSTABLE_WORKTREE")
    post_inspection = _inspect_worktree(
        inspector=filesystem_inspector, worktree=facts.worktree
    )
    if post_inspection != pre_inspection:
        _fail("PHYSICAL_RELEASE_SEAL_UNSTABLE_WORKTREE")
    image_set_sha256 = _image_set_sha256(facts.images)
    release_bundle_sha256 = _release_bundle_sha256(
        release_sha=facts.release_sha,
        git_tree_id=git_tree_id,
        tracked_tree_sha256=tracked_tree_sha256,
    )
    release_provenance_sha256 = _release_provenance_sha256(
        campaign_id=facts.campaign_id,
        release_sha=facts.release_sha,
        git_tree_id=git_tree_id,
        tracked_tree_sha256=tracked_tree_sha256,
        release_bundle_sha256=release_bundle_sha256,
        images=facts.images,
        image_set_sha256=image_set_sha256,
    )
    provisional = PhysicalReleaseSealDescriptorProjection(
        campaign_id=facts.campaign_id,
        release_sha=facts.release_sha,
        control_release_sha=facts.release_sha,
        git_tree_id=git_tree_id,
        tracked_tree_sha256=tracked_tree_sha256,
        release_bundle_sha256=release_bundle_sha256,
        images=facts.images,
        image_set_sha256=image_set_sha256,
        release_provenance_sha256=release_provenance_sha256,
        seal_id=facts.seal_id,
        sealed_at=facts.sealed_at,
        descriptor_sha256="",
    )
    canonical_descriptor, descriptor_sha256 = _descriptor_bytes(provisional)
    projection = PhysicalReleaseSealDescriptorProjection(
        **{
            field_name: getattr(provisional, field_name)
            for field_name in (
                "campaign_id",
                "release_sha",
                "control_release_sha",
                "git_tree_id",
                "tracked_tree_sha256",
                "release_bundle_sha256",
                "images",
                "image_set_sha256",
                "release_provenance_sha256",
                "seal_id",
                "sealed_at",
            )
        },
        descriptor_sha256=descriptor_sha256,
    )
    # Reparse the emitted bytes before returning so the minting and external
    # parser paths share one fail-closed canonicality check.
    parsed = _parse_descriptor(canonical_descriptor).projection
    if parsed != projection:
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_INVALID")
    result = SealedPhysicalReleaseDescriptor(
        canonical_descriptor=canonical_descriptor,
        descriptor_sha256=projection.descriptor_sha256,
        campaign_id=projection.campaign_id,
        release_sha=projection.release_sha,
        control_release_sha=projection.control_release_sha,
        git_tree_id=projection.git_tree_id,
        tracked_tree_sha256=projection.tracked_tree_sha256,
        release_bundle_sha256=projection.release_bundle_sha256,
        image_set_sha256=projection.image_set_sha256,
        release_provenance_sha256=projection.release_provenance_sha256,
        seal_id=projection.seal_id,
        sealed_at=projection.sealed_at,
    )
    object.__setattr__(result, "_capability", _SEALED_DESCRIPTOR_CAPABILITY)
    return result


def require_sealed_physical_release_descriptor(
    value: object,
    *,
    now: datetime,
    maximum_freshness_seconds: int = DEFAULT_PHYSICAL_RELEASE_SEAL_MAX_FRESHNESS_SECONDS,
) -> SealedPhysicalReleaseDescriptor:
    """Require a fresh opaque descriptor; it remains non-authorizing."""

    if (
        type(maximum_freshness_seconds) is not int
        or not 1
        <= maximum_freshness_seconds
        <= MAX_PHYSICAL_RELEASE_SEAL_MAX_FRESHNESS_SECONDS
    ):
        _fail("PHYSICAL_RELEASE_SEAL_CONFIG_INVALID")
    assessed_at = _utc(now, code="PHYSICAL_RELEASE_SEAL_CLOCK_INVALID")
    if (
        type(value) is not SealedPhysicalReleaseDescriptor
        or value._capability is not _SEALED_DESCRIPTOR_CAPABILITY
        or value.publish_authorized is not False
        or value.deployment_authorized is not False
        or value.execution_authorized is not False
    ):
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_REQUIRED")
    facts = _parse_descriptor(value.canonical_descriptor)
    projection = facts.projection
    if (
        value.descriptor_sha256 != projection.descriptor_sha256
        or value.campaign_id != projection.campaign_id
        or value.release_sha != projection.release_sha
        or value.control_release_sha != projection.control_release_sha
        or value.git_tree_id != projection.git_tree_id
        or value.tracked_tree_sha256 != projection.tracked_tree_sha256
        or value.release_bundle_sha256 != projection.release_bundle_sha256
        or value.image_set_sha256 != projection.image_set_sha256
        or value.release_provenance_sha256 != projection.release_provenance_sha256
        or value.seal_id != projection.seal_id
        or value.sealed_at != projection.sealed_at
    ):
        _fail("PHYSICAL_RELEASE_SEAL_DESCRIPTOR_TAMPERED")
    _fresh_seal(
        projection.sealed_at,
        now=assessed_at,
        maximum_freshness_seconds=maximum_freshness_seconds,
        code="PHYSICAL_RELEASE_SEAL_DESCRIPTOR_STALE",
    )
    return value


def project_physical_release_seal_for_wa_ir_bootstrap(
    value: object,
    *,
    now: datetime,
    maximum_freshness_seconds: int = DEFAULT_PHYSICAL_RELEASE_SEAL_MAX_FRESHNESS_SECONDS,
):
    """Return the raw, still non-authorizing binding accepted by the WA-IR seal.

    The caller must explicitly pass this projection to
    ``seal_wa_ir_bootstrap_exact_release_binding``.  This module neither calls
    that function nor publishes a bundle.
    """

    sealed = require_sealed_physical_release_descriptor(
        value,
        now=now,
        maximum_freshness_seconds=maximum_freshness_seconds,
    )
    # Local import keeps this module usable while the downstream bootstrap
    # builder is not imported by unrelated source/image admission paths.
    from core.physical_wa_ir_bootstrap_bundle_builder import (
        WaIrBootstrapExactReleaseBinding,
    )

    return WaIrBootstrapExactReleaseBinding(
        campaign_id=sealed.campaign_id,
        release_sha=sealed.release_sha,
        control_release_sha=sealed.control_release_sha,
        release_bundle_sha256=sealed.release_bundle_sha256,
        image_set_sha256=sealed.image_set_sha256,
        release_provenance_sha256=sealed.release_provenance_sha256,
        source_site=_SOURCE_SITE,
        destination_site=_DESTINATION_SITE,
        seal_id=sealed.seal_id,
        sealed_at=sealed.sealed_at,
    )
