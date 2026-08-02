#!/usr/bin/env python3
"""Build one local, signed WA-FI fenced-release v2 identity descriptor.

This is deliberately a *local* descriptor-construction boundary.  It never
contacts a peer, Object Storage, a registry, or the Writer Witness; it never
builds, pulls, loads, starts, stops, or removes a Docker resource.  The only
Docker operation it performs is a bounded ``docker image inspect`` of the two
already-preloaded candidate images.

The output is a newly-created root-only canonical JSON document.  It is still
non-authorizing: the independent preflight, runtime image attestation and
Writer Witness term remain required before any writer can start.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import select
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from core import fenced_fi_release_identity as identity_contract  # noqa: E402
from core import term_fenced_application_capability as application_capability  # noqa: E402
from scripts import verify_term_fenced_application_source as source_verifier  # noqa: E402


MAX_SMALL_FILE_BYTES = 64 * 1024
MAX_COMPOSE_BYTES = 4 * 1024 * 1024
MAX_DOCKER_INSPECT_BYTES = 256 * 1024
SHA1_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
IMAGE_REF_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,127})?$"
)
IMAGE_REPO_DIGEST_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,511}@sha256:[0-9a-f]{64}$", re.ASCII
)
FENCED_COMPOSE_RELATIVE_PATH = Path(
    "deploy/production/docker-compose.webapp-fi-writer-2c08.yml"
)
SIGNING_DOMAIN = b"gold-trade-wa-fi-fenced-release-identity-v2\x00"
SAFE_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
}
SAFE_DOCKER_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
LOCAL_DOCKER_SOCKET = "unix:///var/run/docker.sock"


class BuildFencedFiReleaseIdentityError(RuntimeError):
    """A local input cannot safely be admitted into a signed identity."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SourceRelease:
    root: Path
    release_sha: str
    release_tree_sha: str
    evidence_sha256: str


@dataclass(frozen=True)
class ControlRelease:
    root: Path
    release_sha: str
    release_tree_sha: str
    compose_sha256: str


@dataclass(frozen=True)
class LocalImageIdentity:
    service: str
    image_ref: str
    image_repo_digest: str
    image_id: str


@dataclass(frozen=True)
class BuiltFencedFiReleaseIdentity:
    document: bytes
    identity_sha256: str
    source: SourceRelease
    control: ControlRelease
    app: LocalImageIdentity
    bot: LocalImageIdentity
    signer_key_id: str


def _fail(code: str) -> None:
    raise BuildFencedFiReleaseIdentityError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("FENCED_FI_RELEASE_DESCRIPTOR_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    _fail("FENCED_FI_RELEASE_DESCRIPTOR_DOCKER_IMAGE_INSPECTION_REJECTED")


def _absolute_path(value: Path, *, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        _fail(f"FENCED_FI_RELEASE_DESCRIPTOR_{label}_PATH_INVALID")
    return path


def _require_root_controlled_directory_chain(path: Path, *, label: str) -> Path:
    """Reject symlinked or attacker-writable ancestors before path use.

    A root-owned directory below a non-sticky writable ancestor can otherwise
    be renamed or replaced between an ``lstat`` and a later open.  The one
    standard exception is a root-owned sticky directory such as ``/tmp``:
    non-root users cannot rename a root-owned entry from it, so private
    root-owned descendants remain safe for this local construction tool.
    """

    path = _absolute_path(path, label=label)
    current = Path(path.root)
    components = path.parts[1:]
    for component in ("", *components):
        if component:
            current /= component
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            raise BuildFencedFiReleaseIdentityError(
                f"FENCED_FI_RELEASE_DESCRIPTOR_{label}_ANCESTOR_UNAVAILABLE"
            ) from exc
        mode = stat.S_IMODE(metadata.st_mode)
        writable_by_others = mode & 0o022
        root_sticky_directory = (
            stat.S_ISDIR(metadata.st_mode)
            and metadata.st_uid == 0
            and bool(mode & stat.S_ISVTX)
        )
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or (writable_by_others and not root_sticky_directory)
        ):
            _fail(f"FENCED_FI_RELEASE_DESCRIPTOR_{label}_ANCESTOR_UNSAFE")
    return path


def _require_root() -> None:
    if os.geteuid() != 0:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_ROOT_REQUIRED")


def _require_root_controlled_directory(path: Path, *, label: str) -> Path:
    path = _require_root_controlled_directory_chain(path, label=label)
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise BuildFencedFiReleaseIdentityError(
            f"FENCED_FI_RELEASE_DESCRIPTOR_{label}_UNAVAILABLE"
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        _fail(f"FENCED_FI_RELEASE_DESCRIPTOR_{label}_UNSAFE")
    return path


def _secure_read(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
    private: bool,
) -> bytes:
    """Read a stable, root-controlled regular file without following links."""

    path = _absolute_path(path, label=label)
    _require_root_controlled_directory_chain(path.parent, label=label)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if type(no_follow) is not int:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_O_NOFOLLOW_REQUIRED")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0) | no_follow
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BuildFencedFiReleaseIdentityError(
            f"FENCED_FI_RELEASE_DESCRIPTOR_{label}_UNAVAILABLE"
        ) from exc
    try:
        before = os.fstat(descriptor)
        unsafe_mode = 0o077 if private else 0o022
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & unsafe_mode
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > maximum_bytes
        ):
            _fail(f"FENCED_FI_RELEASE_DESCRIPTOR_{label}_UNSAFE")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                _fail(f"FENCED_FI_RELEASE_DESCRIPTOR_{label}_SHORT_READ")
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        identity = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(getattr(before, field) != getattr(after, field) for field in identity):
            _fail(f"FENCED_FI_RELEASE_DESCRIPTOR_{label}_CHANGED")
        return payload
    finally:
        os.close(descriptor)


def _trusted_executable(path: Path, *, label: str) -> Path:
    path = _absolute_path(path, label=label)
    _require_root_controlled_directory_chain(path.parent, label=label)
    try:
        metadata = os.stat(path)
    except OSError as exc:
        raise BuildFencedFiReleaseIdentityError(
            f"FENCED_FI_RELEASE_DESCRIPTOR_{label}_UNAVAILABLE"
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        _fail(f"FENCED_FI_RELEASE_DESCRIPTOR_{label}_UNSAFE")
    return path


def _run_bounded_command(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path | None,
    maximum_bytes: int,
    unavailable_code: str,
    rejected_code: str,
) -> bytes:
    """Run one fixed local command without ever buffering unbounded stdout."""

    if (
        not command
        or maximum_bytes < 1
        or not all(isinstance(item, str) and item for item in command)
        or not isinstance(env, Mapping)
        or not all(isinstance(key, str) and isinstance(value, str) for key, value in env.items())
    ):
        _fail(rejected_code)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=dict(env),
            cwd=str(cwd) if cwd is not None else None,
        )
        if process.stdout is None:  # pragma: no cover - PIPE is fixed above.
            _fail(rejected_code)
        deadline = time.monotonic() + 20
        chunks: list[bytes] = []
        total = 0
        descriptor = process.stdout.fileno()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail(unavailable_code)
            ready, _write_ready, _errors = select.select([descriptor], [], [], remaining)
            if not ready:
                _fail(unavailable_code)
            chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                _fail(rejected_code)
            chunks.append(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            _fail(rejected_code)
        return b"".join(chunks)
    except BuildFencedFiReleaseIdentityError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise BuildFencedFiReleaseIdentityError(unavailable_code) from exc
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.wait(timeout=1)
            except (OSError, subprocess.SubprocessError):
                pass
            if process.stdout is not None:
                process.stdout.close()


def _run_git(root: Path, *arguments: str, maximum_bytes: int) -> bytes:
    git = _trusted_executable(Path("/usr/bin/git"), label="GIT")
    return _run_bounded_command(
        [str(git), "-C", str(root), *arguments],
        env=SAFE_GIT_ENV,
        cwd=None,
        maximum_bytes=maximum_bytes,
        unavailable_code="FENCED_FI_RELEASE_DESCRIPTOR_GIT_UNAVAILABLE",
        rejected_code="FENCED_FI_RELEASE_DESCRIPTOR_GIT_REJECTED",
    )


def _git_one_line(root: Path, *arguments: str) -> str:
    try:
        value = _run_git(root, *arguments, maximum_bytes=4096).decode("ascii")
    except UnicodeDecodeError as exc:
        raise BuildFencedFiReleaseIdentityError(
            "FENCED_FI_RELEASE_DESCRIPTOR_GIT_REJECTED"
        ) from exc
    value = value.strip()
    if not value or "\n" in value:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_GIT_REJECTED")
    return value


def _git_identity(root: Path, *, label: str) -> tuple[str, str]:
    release_sha = _git_one_line(root, "rev-parse", "--verify", "HEAD")
    release_tree_sha = _git_one_line(root, "rev-parse", "--verify", "HEAD^{tree}")
    if SHA1_RE.fullmatch(release_sha) is None or SHA1_RE.fullmatch(release_tree_sha) is None:
        _fail(f"FENCED_FI_RELEASE_DESCRIPTOR_{label}_GIT_IDENTITY_INVALID")
    return release_sha, release_tree_sha


def _require_clean_git_tree(root: Path, *, label: str) -> tuple[str, str]:
    release_sha, release_tree_sha = _git_identity(root, label=label)
    if _run_git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        maximum_bytes=MAX_COMPOSE_BYTES,
    ):
        _fail(f"FENCED_FI_RELEASE_DESCRIPTOR_{label}_WORKTREE_DIRTY")
    return release_sha, release_tree_sha


def _load_authority(path: Path) -> tuple[bytes, identity_contract.FencedFiReleaseIdentityAuthority]:
    raw = _secure_read(
        path,
        label="AUTHORITY_PUBLIC_KEY",
        maximum_bytes=256,
        private=True,
    )
    try:
        text = raw.decode("ascii")
        if text.endswith("\n"):
            text = text[:-1]
        if not text or text != text.strip() or "\n" in text:
            raise ValueError
        public = base64.b64decode(text.encode("ascii"), validate=True)
        if base64.b64encode(public).decode("ascii") != text:
            raise ValueError
    except (UnicodeDecodeError, ValueError) as exc:
        raise BuildFencedFiReleaseIdentityError(
            "FENCED_FI_RELEASE_DESCRIPTOR_AUTHORITY_PUBLIC_KEY_INVALID"
        ) from exc
    if len(public) != 32:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_AUTHORITY_PUBLIC_KEY_INVALID")
    authority = identity_contract.FencedFiReleaseIdentityAuthority(
        public_key=public,
        key_id="ed25519-sha256:" + hashlib.sha256(public).hexdigest(),
    )
    return public, authority


def _load_signer(path: Path, *, expected_public_key: bytes) -> Ed25519PrivateKey:
    raw = _secure_read(
        path,
        label="SIGNING_PRIVATE_KEY",
        maximum_bytes=32,
        private=True,
    )
    if len(raw) != 32:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_SIGNING_PRIVATE_KEY_INVALID")
    try:
        signer = Ed25519PrivateKey.from_private_bytes(raw)
        public = signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except ValueError as exc:
        raise BuildFencedFiReleaseIdentityError(
            "FENCED_FI_RELEASE_DESCRIPTOR_SIGNING_PRIVATE_KEY_INVALID"
        ) from exc
    if public != expected_public_key:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_SIGNING_KEY_AUTHORITY_MISMATCH")
    return signer


def _load_clean_application_source_tree(root: Path) -> source_verifier.SourceTree:
    """Read capability inputs from bounded immutable Git blobs only.

    The shared source verifier exposes the semantic AST/evidence check.  Its
    generic Git loader predates this builder's streaming-cap requirement, so
    this local wrapper performs the same clean-tree/blob admission through
    :func:`_run_git` before passing a closed ``SourceTree`` to that verifier.
    """

    release_sha, release_tree_sha = _require_clean_git_tree(
        root,
        label="APPLICATION_RELEASE",
    )
    blobs: dict[str, bytes] = {}
    for relative in sorted(application_capability.TERM_FENCED_APPLICATION_CAPABILITY_FILES):
        blob = _run_git(
            root,
            "show",
            f"HEAD:{relative}",
            maximum_bytes=source_verifier.MAX_SOURCE_FILE_BYTES,
        )
        if not blob:
            _fail("FENCED_FI_RELEASE_DESCRIPTOR_APPLICATION_RELEASE_SOURCE_FILE_INVALID")
        blobs[relative] = blob
    final_sha, final_tree_sha = _require_clean_git_tree(
        root,
        label="APPLICATION_RELEASE",
    )
    if (release_sha, release_tree_sha) != (final_sha, final_tree_sha):
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_APPLICATION_RELEASE_CHANGED")
    return source_verifier.SourceTree(
        root=root,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        blobs=blobs,
    )


def _load_source_release(
    root: Path,
    *,
    evidence_document: bytes,
) -> SourceRelease:
    root = _require_root_controlled_directory(root, label="APPLICATION_RELEASE_ROOT")
    # The semantic source verifier deliberately consumes Git blobs rather
    # than mutable worktree files.  The wrapper also streams every Git output
    # under an explicit cap before semantic validation runs.
    try:
        tree = _load_clean_application_source_tree(root)
        evidence = application_capability.verify_term_fenced_application_capability(
            evidence_document
        )
        source_verifier.verify_evidence_for_source(tree, evidence_document)
    except BuildFencedFiReleaseIdentityError:
        raise
    except (
        source_verifier.TermFencedApplicationSourceError,
        application_capability.TermFencedApplicationCapabilityError,
    ) as exc:
        raise BuildFencedFiReleaseIdentityError(
            "FENCED_FI_RELEASE_DESCRIPTOR_TERM_FENCED_EVIDENCE_INVALID"
        ) from exc
    if root.name != tree.release_sha:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_APPLICATION_RELEASE_ROOT_IMMUTABLE_PATH_REQUIRED")
    if evidence.release_sha != tree.release_sha or evidence.release_tree_sha != tree.release_tree_sha:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_TERM_FENCED_EVIDENCE_INVALID")
    return SourceRelease(
        root=root,
        release_sha=tree.release_sha,
        release_tree_sha=tree.release_tree_sha,
        evidence_sha256=evidence.evidence_sha256,
    )


def _load_control_release(root: Path) -> ControlRelease:
    root = _require_root_controlled_directory(root, label="CONTROL_RELEASE_ROOT")
    before_sha, before_tree = _require_clean_git_tree(root, label="CONTROL_RELEASE")
    if root.name != before_sha:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_CONTROL_RELEASE_ROOT_IMMUTABLE_PATH_REQUIRED")
    relative = str(FENCED_COMPOSE_RELATIVE_PATH)
    compose_from_git = _run_git(root, "show", f"HEAD:{relative}", maximum_bytes=MAX_COMPOSE_BYTES)
    if not compose_from_git:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_COMPOSE_INVALID")
    compose_from_worktree = _secure_read(
        root / FENCED_COMPOSE_RELATIVE_PATH,
        label="COMPOSE_FILE",
        maximum_bytes=MAX_COMPOSE_BYTES,
        private=False,
    )
    if compose_from_worktree != compose_from_git:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_COMPOSE_WORKTREE_MISMATCH")
    after_sha, after_tree = _require_clean_git_tree(root, label="CONTROL_RELEASE")
    if (before_sha, before_tree) != (after_sha, after_tree):
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_CONTROL_RELEASE_CHANGED")
    return ControlRelease(
        root=root,
        release_sha=before_sha,
        release_tree_sha=before_tree,
        compose_sha256=hashlib.sha256(compose_from_git).hexdigest(),
    )


def _require_image_ref(value: str, *, service: str) -> str:
    if not isinstance(value, str) or not IMAGE_REF_RE.fullmatch(value) or value.startswith("-"):
        _fail(f"FENCED_FI_RELEASE_DESCRIPTOR_{service}_IMAGE_REFERENCE_INVALID")
    return value


def _require_repo_digest(value: str, *, service: str) -> str:
    if not isinstance(value, str) or IMAGE_REPO_DIGEST_RE.fullmatch(value) is None:
        _fail(f"FENCED_FI_RELEASE_DESCRIPTOR_{service}_REPO_DIGEST_INVALID")
    return value


def _run_docker_image_inspect(image_ref: str) -> Mapping[str, Any]:
    """Return one bounded, local Docker image-inspection object.

    A fixed executable, an explicit local Unix socket and a sanitized
    environment make this a local read-only inventory call.  In particular,
    it must not inherit a root user's configured Docker context.  ``docker
    image inspect`` never pulls from a registry.
    """

    docker = _trusted_executable(Path("/usr/bin/docker"), label="DOCKER")
    raw = _run_bounded_command(
        [
            str(docker),
            "--host",
            LOCAL_DOCKER_SOCKET,
            "image",
            "inspect",
            "--format",
            "{{json .}}",
            image_ref,
        ],
        env=SAFE_DOCKER_ENV,
        cwd=None,
        maximum_bytes=MAX_DOCKER_INSPECT_BYTES,
        unavailable_code="FENCED_FI_RELEASE_DESCRIPTOR_DOCKER_UNAVAILABLE",
        rejected_code="FENCED_FI_RELEASE_DESCRIPTOR_DOCKER_IMAGE_INSPECTION_REJECTED",
    )
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except BuildFencedFiReleaseIdentityError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise BuildFencedFiReleaseIdentityError(
            "FENCED_FI_RELEASE_DESCRIPTOR_DOCKER_IMAGE_INSPECTION_REJECTED"
        ) from exc
    if not isinstance(value, Mapping):
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_DOCKER_IMAGE_INSPECTION_REJECTED")
    return value


def _inspect_local_image(
    *,
    service: str,
    image_ref: str,
    expected_repo_digest: str,
    evidence_document: bytes,
) -> LocalImageIdentity:
    image_ref = _require_image_ref(image_ref, service=service)
    expected_repo_digest = _require_repo_digest(expected_repo_digest, service=service)
    value = _run_docker_image_inspect(image_ref)
    image_id = value.get("Id")
    if not isinstance(image_id, str) or IMAGE_ID_RE.fullmatch(image_id.lower()) is None:
        _fail(f"FENCED_FI_RELEASE_DESCRIPTOR_{service}_IMAGE_ID_INVALID")
    repo_digests = value.get("RepoDigests")
    if (
        not isinstance(repo_digests, list)
        or not repo_digests
        or not all(isinstance(item, str) and IMAGE_REPO_DIGEST_RE.fullmatch(item) for item in repo_digests)
        or len(set(repo_digests)) != len(repo_digests)
        or expected_repo_digest not in repo_digests
    ):
        _fail(f"FENCED_FI_RELEASE_DESCRIPTOR_{service}_REPO_DIGEST_NOT_LOCAL")
    config = value.get("Config")
    labels = config.get("Labels") if isinstance(config, Mapping) else None
    if not isinstance(labels, Mapping) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in labels.items()
    ):
        _fail(f"FENCED_FI_RELEASE_DESCRIPTOR_{service}_IMAGE_LABELS_INVALID")
    # Labels complement, but never replace, the signed image ID + RepoDigest.
    # Checking them here prevents minting an otherwise-valid descriptor for an
    # image that the later preflight would necessarily reject.
    try:
        capability_evidence = application_capability.verify_term_fenced_application_capability(
            evidence_document
        )
    except application_capability.TermFencedApplicationCapabilityError as exc:
        raise BuildFencedFiReleaseIdentityError(
            f"FENCED_FI_RELEASE_DESCRIPTOR_{service}_IMAGE_LABEL_MISMATCH"
        ) from exc
    try:
        application_capability.verify_term_fenced_image_labels(
            labels,
            evidence=capability_evidence,
        )
    except application_capability.TermFencedApplicationCapabilityError as exc:
        raise BuildFencedFiReleaseIdentityError(
            f"FENCED_FI_RELEASE_DESCRIPTOR_{service}_IMAGE_LABEL_MISMATCH"
        ) from exc
    return LocalImageIdentity(
        service=service,
        image_ref=image_ref,
        image_repo_digest=expected_repo_digest,
        image_id=image_id.lower(),
    )


def _same_source(left: SourceRelease, right: SourceRelease) -> bool:
    return left == right


def _same_control(left: ControlRelease, right: ControlRelease) -> bool:
    return left == right


def _same_image(left: LocalImageIdentity, right: LocalImageIdentity) -> bool:
    return left == right


def build_fenced_fi_release_identity(
    *,
    application_release_root: Path,
    control_release_root: Path,
    term_fenced_application_evidence: Path,
    app_image: str,
    app_repo_digest: str,
    bot_image: str,
    bot_repo_digest: str,
    signing_private_key: Path,
    authority_public_key: Path,
) -> BuiltFencedFiReleaseIdentity:
    """Collect two stable local attestations and sign one v2 descriptor.

    Inputs are collected twice.  A changed Git tree, evidence file, local
    image ID, selected RepoDigest, or term-fence label is a fail-closed error;
    a descriptor is never built from a mixed observation.
    """

    _require_root()
    evidence_path = _absolute_path(
        term_fenced_application_evidence,
        label="TERM_FENCED_APPLICATION_EVIDENCE",
    )
    evidence_document = _secure_read(
        evidence_path,
        label="TERM_FENCED_APPLICATION_EVIDENCE",
        maximum_bytes=MAX_SMALL_FILE_BYTES,
        private=True,
    )
    initial_public_key, initial_authority = _load_authority(authority_public_key)
    _load_signer(signing_private_key, expected_public_key=initial_public_key)
    first_source = _load_source_release(
        application_release_root,
        evidence_document=evidence_document,
    )
    first_control = _load_control_release(control_release_root)
    first_app = _inspect_local_image(
        service="APP",
        image_ref=app_image,
        expected_repo_digest=app_repo_digest,
        evidence_document=evidence_document,
    )
    first_bot = _inspect_local_image(
        service="BOT",
        image_ref=bot_image,
        expected_repo_digest=bot_repo_digest,
        evidence_document=evidence_document,
    )

    final_evidence_document = _secure_read(
        evidence_path,
        label="TERM_FENCED_APPLICATION_EVIDENCE",
        maximum_bytes=MAX_SMALL_FILE_BYTES,
        private=True,
    )
    if final_evidence_document != evidence_document:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_TERM_FENCED_EVIDENCE_CHANGED")
    final_source = _load_source_release(
        application_release_root,
        evidence_document=final_evidence_document,
    )
    final_control = _load_control_release(control_release_root)
    final_app = _inspect_local_image(
        service="APP",
        image_ref=app_image,
        expected_repo_digest=app_repo_digest,
        evidence_document=final_evidence_document,
    )
    final_bot = _inspect_local_image(
        service="BOT",
        image_ref=bot_image,
        expected_repo_digest=bot_repo_digest,
        evidence_document=final_evidence_document,
    )
    if not _same_source(first_source, final_source):
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_APPLICATION_RELEASE_CHANGED")
    if not _same_control(first_control, final_control):
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_CONTROL_RELEASE_CHANGED")
    if not _same_image(first_app, final_app) or not _same_image(first_bot, final_bot):
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_LOCAL_IMAGE_CHANGED")

    # Check the signing authority again after the local candidate facts have
    # settled.  A key rotation during construction is a fail-closed event;
    # this tool never signs a mixed candidate under a newly substituted key.
    public_key, authority = _load_authority(authority_public_key)
    if authority != initial_authority:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_SIGNING_AUTHORITY_CHANGED")
    signer = _load_signer(signing_private_key, expected_public_key=public_key)

    unsigned: dict[str, object] = {
        "schema": identity_contract.FENCED_FI_RELEASE_IDENTITY_SCHEMA,
        "release_sha": final_source.release_sha,
        "release_tree_sha": final_source.release_tree_sha,
        "application_release_root": str(final_source.root),
        "control_release_sha": final_control.release_sha,
        "control_release_tree_sha": final_control.release_tree_sha,
        "control_release_root": str(final_control.root),
        "compose_relative_path": str(FENCED_COMPOSE_RELATIVE_PATH),
        "compose_sha256": final_control.compose_sha256,
        "term_fenced_application_evidence_sha256": final_source.evidence_sha256,
        "services": {
            "app": {
                "image_repo_digest": final_app.image_repo_digest,
                "image_id": final_app.image_id,
            },
            "bot": {
                "image_repo_digest": final_bot.image_repo_digest,
                "image_id": final_bot.image_id,
            },
        },
        "signer_key_id": authority.key_id,
    }
    signature = signer.sign(
        SIGNING_DOMAIN
        + identity_contract.canonical_fenced_fi_release_identity_json_bytes(unsigned)
    )
    document = dict(unsigned)
    document["signature_base64"] = base64.b64encode(signature).decode("ascii")
    payload = identity_contract.canonical_fenced_fi_release_identity_json_bytes(document)
    # The exact independent verifier is the final schema/signature guard;
    # never hand a caller a document that it would reject.
    try:
        verified = identity_contract.verify_fenced_fi_release_identity(
            payload,
            authority=authority,
        )
        identity_contract.require_term_fenced_fi_release_candidate(verified)
    except identity_contract.FencedFiReleaseIdentityError as exc:
        raise BuildFencedFiReleaseIdentityError(
            "FENCED_FI_RELEASE_DESCRIPTOR_INTERNAL_VERIFICATION_FAILED"
        ) from exc
    return BuiltFencedFiReleaseIdentity(
        document=payload,
        identity_sha256=hashlib.sha256(payload).hexdigest(),
        source=final_source,
        control=final_control,
        app=final_app,
        bot=final_bot,
        signer_key_id=authority.key_id,
    )


def write_new_fenced_fi_release_identity(path: Path, *, payload: bytes) -> None:
    """Publish one fully-written descriptor without overwriting an existing file.

    The final leaf name is linked only after the unique temporary inode has
    been completely written and fsynced.  A short write or file-fsync error
    therefore cannot leave a partial descriptor at the immutable final path.
    """

    path = _absolute_path(path, label="OUTPUT")
    if not payload or len(payload) > MAX_SMALL_FILE_BYTES:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_OUTPUT_INVALID")
    parent = _require_root_controlled_directory(path.parent, label="OUTPUT_PARENT")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if type(no_follow) is not int:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_O_NOFOLLOW_REQUIRED")
    if path.name in {"", ".", ".."}:
        _fail("FENCED_FI_RELEASE_DESCRIPTOR_OUTPUT_INVALID")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | no_follow
    )
    directory_descriptor = -1
    descriptor = -1
    temporary_name: str | None = None
    published = False
    try:
        directory_descriptor = os.open(parent, directory_flags)
        metadata = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            _fail("FENCED_FI_RELEASE_DESCRIPTOR_OUTPUT_PARENT_UNSAFE")
        for _attempt in range(8):
            candidate = f".{path.name}.tmp-{secrets.token_hex(16)}"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | no_follow,
                    0o600,
                    dir_fd=directory_descriptor,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if descriptor < 0 or temporary_name is None:  # pragma: no cover - cryptographic nonce collision.
            _fail("FENCED_FI_RELEASE_DESCRIPTOR_OUTPUT_UNAVAILABLE")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                _fail("FENCED_FI_RELEASE_DESCRIPTOR_OUTPUT_WRITE_FAILED")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise BuildFencedFiReleaseIdentityError(
                "FENCED_FI_RELEASE_DESCRIPTOR_OUTPUT_EXISTS"
            ) from exc
        published = True
        os.fsync(directory_descriptor)
        os.unlink(temporary_name, dir_fd=directory_descriptor)
        temporary_name = None
        os.fsync(directory_descriptor)
    except BuildFencedFiReleaseIdentityError:
        raise
    except OSError as exc:
        raise BuildFencedFiReleaseIdentityError(
            "FENCED_FI_RELEASE_DESCRIPTOR_OUTPUT_UNAVAILABLE"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name is not None and not published and directory_descriptor >= 0:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except OSError:
                pass
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-release-root", required=True, type=Path)
    parser.add_argument("--control-release-root", required=True, type=Path)
    parser.add_argument("--term-fenced-application-evidence", required=True, type=Path)
    parser.add_argument("--app-image", required=True)
    parser.add_argument("--app-repo-digest", required=True)
    parser.add_argument("--bot-image", required=True)
    parser.add_argument("--bot-repo-digest", required=True)
    parser.add_argument("--signing-private-key", required=True, type=Path)
    parser.add_argument("--authority-public-key", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        built = build_fenced_fi_release_identity(
            application_release_root=arguments.application_release_root,
            control_release_root=arguments.control_release_root,
            term_fenced_application_evidence=arguments.term_fenced_application_evidence,
            app_image=arguments.app_image,
            app_repo_digest=arguments.app_repo_digest,
            bot_image=arguments.bot_image,
            bot_repo_digest=arguments.bot_repo_digest,
            signing_private_key=arguments.signing_private_key,
            authority_public_key=arguments.authority_public_key,
        )
        write_new_fenced_fi_release_identity(arguments.output, payload=built.document)
    except BuildFencedFiReleaseIdentityError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_class": type(exc).__name__,
                    "error": exc.code,
                },
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "created-non-authorizing",
                "schema": identity_contract.FENCED_FI_RELEASE_IDENTITY_SCHEMA,
                "identity_sha256": built.identity_sha256,
                "release_sha": built.source.release_sha,
                "release_tree_sha": built.source.release_tree_sha,
                "control_release_sha": built.control.release_sha,
                "control_release_tree_sha": built.control.release_tree_sha,
                "compose_sha256": built.control.compose_sha256,
                "term_fenced_application_evidence_sha256": built.source.evidence_sha256,
                "app_image_repo_digest": built.app.image_repo_digest,
                "app_image_id": built.app.image_id,
                "bot_image_repo_digest": built.bot.image_repo_digest,
                "bot_image_id": built.bot.image_id,
                "signer_key_id": built.signer_key_id,
                "output": str(arguments.output),
                "writer_authorized": False,
                "promotion_authorized": False,
                "execution_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
