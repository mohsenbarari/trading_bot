#!/usr/bin/env python3
"""Build and seal the local-only Emergency IR Docker image bundle.

This program deliberately has no Object Storage, SSH, registry, DNS, or
Compose client.  It is intended to run *on WA-FI* after the Emergency source
checkout and the already-built frontend distribution are available locally.

The Docker build context is created afresh from committed Git blobs plus two
explicit, read-only inputs (frontend distribution and wheelhouse).  This
avoids accidentally shipping an ignored worktree file, a staging artifact, or
a mutable development frontend.  The resulting application, PostgreSQL, and
Redis images are then saved through an O_EXCL-owned file descriptor and bound
to a create-only JSON receipt.  A later, separately approved publisher is the
only component allowed to transfer that bundle anywhere.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
from typing import Any, Callable, Iterable, Sequence


MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from scripts import verify_emergency_ir_image_provenance as app_provenance  # noqa: E402


REPO_ROOT = MODULE_ROOT
SOURCE_RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
PYTHON_BASE_IMAGE = "python:3.11-slim-bullseye"
APP_REPOSITORY = "trading_bot_emergency_ir_app"
POSTGRES_REPOSITORY = "trading_bot_emergency_ir_postgres"
REDIS_REPOSITORY = "trading_bot_emergency_ir_redis"
CONTEXT_FRONTEND_DIRECTORY = "mini_app_dist"
CONTEXT_WHEELHOUSE_DIRECTORY = "pip_packages"
RECEIPT_SCHEMA = "gold-trade-emergency-ir-image-bundle-v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
CONFIG_NAME_RE = re.compile(r"^[0-9a-f]{64}\.json$", re.ASCII)
FORBIDDEN_TAG_MARKERS = ("staging", "three_site")

# These are exactly the source paths copied by the repository Dockerfile.
CONTEXT_GIT_PATHS = (
    "Dockerfile",
    "requirements.txt",
    "api",
    "bot",
    "core",
    "src",
    "migrations",
    "models",
    "templates",
    "fonts",
    "alembic.ini",
    "main.py",
    "manage.py",
    "run_bot.py",
    "schemas.py",
    "seed_fake_data.py",
    "scripts",
)
DOCKERFILE_CONTRACT = (
    "FROM python:3.11-slim-bullseye",
    "COPY pip_packages/ /tmp/pip_packages/",
    "ARG FRONTEND_DIST_DIR=mini_app_dist",
    "COPY ${FRONTEND_DIST_DIR}/ /app/mini_app_dist/",
)
MAX_INPUT_FILES = 100_000
MAX_FRONTEND_BYTES = 512 * 1024 * 1024
MAX_WHEELHOUSE_BYTES = 1024 * 1024 * 1024
MAX_CONTEXT_BYTES = 2 * 1024 * 1024 * 1024
MAX_BUNDLE_BYTES = 16 * 1024 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024


class EmergencyImageBundleError(RuntimeError):
    """A local Emergency image bundle invariant was not satisfied."""


@dataclasses.dataclass(frozen=True)
class GitBlob:
    path: str
    object_id: str
    mode: str


@dataclasses.dataclass(frozen=True)
class TreeIdentity:
    sha256: str
    bytes: int
    files: int


@dataclasses.dataclass(frozen=True)
class ImageIdentity:
    reference: str
    image_id: str
    repo_tags: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class BundleResult:
    receipt: dict[str, Any]
    receipt_sha256: str


Runner = Callable[..., Any]


def _fail(message: str) -> None:
    raise EmergencyImageBundleError(message)


def _as_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8", "strict")
    return b""


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return ""


def _run(
    command: Sequence[str],
    *,
    runner: Runner,
    text: bool = True,
    timeout: int = 60,
    **kwargs: Any,
) -> Any:
    try:
        return runner(
            list(command),
            check=False,
            capture_output=True,
            text=text,
            timeout=timeout,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyImageBundleError("required local command could not be executed") from exc


def _run_success(
    command: Sequence[str],
    *,
    runner: Runner,
    purpose: str,
    text: bool = True,
    timeout: int = 60,
) -> Any:
    result = _run(command, runner=runner, text=text, timeout=timeout)
    if getattr(result, "returncode", 1) != 0:
        _fail(f"{purpose} failed")
    return result


def _safe_relative(value: str, *, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        _fail(f"{label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        _fail(f"{label} escapes the immutable context")
    return path


def _require_absolute(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        _fail(f"{label} must be absolute")
    return path


def _require_no_symlink_ancestors(path: Path, *, label: str) -> None:
    """Reject a symlink in the path without requiring /tmp itself to be private."""

    current = Path("/")
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise EmergencyImageBundleError(f"{label} cannot be inspected") from exc
        if stat.S_ISLNK(metadata.st_mode):
            _fail(f"{label} has a symlink ancestor")
        if current != path and not stat.S_ISDIR(metadata.st_mode):
            _fail(f"{label} has a non-directory ancestor")


def _require_safe_parent(path: Path, *, label: str) -> None:
    path = _require_absolute(path, label=label)
    parent = path.parent
    _require_no_symlink_ancestors(parent, label=label)
    try:
        metadata = parent.lstat()
    except OSError as exc:
        raise EmergencyImageBundleError(f"{label} parent cannot be inspected") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        _fail(f"{label} parent is not owner-controlled")


def _require_absent(path: Path, *, label: str) -> None:
    _require_safe_parent(path, label=label)
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise EmergencyImageBundleError(f"{label} cannot be inspected") from exc
    _fail(f"refusing to overwrite existing {label}")


def _write_create_only(path: Path, payload: bytes, *, label: str) -> None:
    _require_absent(path, label=label)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError("output write made no progress")
            offset += written
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise EmergencyImageBundleError(f"refusing to overwrite existing {label}") from exc
    except OSError as exc:
        raise EmergencyImageBundleError(f"{label} cannot be created") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _canonical_json(value: Any) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError) as exc:
        raise EmergencyImageBundleError("receipt cannot be canonicalized") from exc


def _validate_image_reference(value: str, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(character.isspace() or ord(character) < 33 for character in value)
        or any(marker in value.lower() for marker in FORBIDDEN_TAG_MARKERS)
    ):
        _fail(f"{label} image reference is invalid or forbidden")
    return value


def _assert_clean_checkout(*, repo: Path, runner: Runner) -> str:
    repo = _require_absolute(repo, label="repository")
    top = _run_success(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
        runner=runner,
        purpose="repository discovery",
    )
    if Path(_as_text(getattr(top, "stdout", "")).strip()).resolve() != repo.resolve():
        _fail("repository must be the checkout root")
    status = _run_success(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"],
        runner=runner,
        purpose="repository cleanliness check",
    )
    if _as_text(getattr(status, "stdout", "")).strip():
        _fail("repository checkout is dirty")
    head = _as_text(
        getattr(
            _run_success(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                runner=runner,
                purpose="repository HEAD discovery",
            ),
            "stdout",
            "",
        )
    ).strip()
    if SHA_RE.fullmatch(head) is None:
        _fail("repository HEAD is not an exact lowercase Git SHA")
    ancestor = _run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", SOURCE_RELEASE_SHA, head],
        runner=runner,
    )
    if getattr(ancestor, "returncode", 1) != 0:
        _fail("repository HEAD is not descended from the attested production base")
    return head


def _git_blobs(*, repo: Path, runner: Runner) -> list[GitBlob]:
    result = _run_success(
        ["git", "-C", str(repo), "ls-tree", "-r", "-z", "--full-tree", "HEAD", "--", *CONTEXT_GIT_PATHS],
        runner=runner,
        purpose="immutable context file listing",
        text=False,
    )
    records = [record for record in _as_bytes(getattr(result, "stdout", b"")).split(b"\0") if record]
    blobs: list[GitBlob] = []
    for record in records:
        try:
            header, raw_path = record.split(b"\t", 1)
            mode, kind, object_id = header.decode("ascii").split(" ", 2)
            relative = raw_path.decode("utf-8", "strict")
        except (UnicodeDecodeError, ValueError) as exc:
            raise EmergencyImageBundleError("immutable context Git listing is malformed") from exc
        _safe_relative(relative, label="immutable context path")
        if kind != "blob" or mode not in {"100644", "100755"} or not re.fullmatch(r"[0-9a-f]{40,64}", object_id):
            _fail("immutable context contains an unsupported Git entry")
        if not any(relative == item or relative.startswith(item.rstrip("/") + "/") for item in CONTEXT_GIT_PATHS):
            _fail("immutable context Git listing escaped the Dockerfile allowlist")
        blobs.append(GitBlob(path=relative, object_id=object_id, mode=mode))
    if not blobs:
        _fail("immutable context Git listing is empty")
    paths = {blob.path for blob in blobs}
    for required in CONTEXT_GIT_PATHS:
        if required in {"Dockerfile", "requirements.txt", "alembic.ini", "main.py", "manage.py", "run_bot.py", "schemas.py", "seed_fake_data.py"}:
            present = required in paths
        else:
            present = any(path.startswith(required + "/") for path in paths)
        if not present:
            _fail(f"immutable context is missing required Dockerfile source: {required}")
    return sorted(blobs, key=lambda blob: blob.path)


def _read_head_blob(*, repo: Path, relative: str, runner: Runner) -> bytes:
    result = _run_success(
        ["git", "-C", str(repo), "show", f"HEAD:{relative}"],
        runner=runner,
        purpose="immutable context Git blob read",
        text=False,
    )
    payload = _as_bytes(getattr(result, "stdout", b""))
    return payload


def _validate_dockerfile_contract(*, repo: Path, runner: Runner) -> None:
    try:
        source = _read_head_blob(repo=repo, relative="Dockerfile", runner=runner).decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise EmergencyImageBundleError("Dockerfile is not UTF-8") from exc
    if any(marker not in source for marker in DOCKERFILE_CONTRACT):
        _fail("Dockerfile no longer matches the Emergency application image contract")


def _make_context_directory(path: Path) -> None:
    _require_absent(path, label="immutable build context")
    try:
        path.mkdir(mode=0o700)
        path.chmod(0o700)
    except OSError as exc:
        raise EmergencyImageBundleError("immutable build context cannot be created") from exc


def _ensure_context_parent(context: Path, relative: PurePosixPath) -> Path:
    target = context.joinpath(*relative.parts)
    parent = target.parent
    parent.mkdir(mode=0o555, parents=True, exist_ok=True)
    current = context
    for part in relative.parts[:-1]:
        current /= part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            _fail("immutable build context has an unsafe directory")
        current.chmod(0o555)
    return target


def _write_context_file(path: Path, payload: bytes) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o444,
        )
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError("context write made no progress")
            offset += written
        os.fchmod(descriptor, 0o444)
        os.fsync(descriptor)
    except (FileExistsError, OSError) as exc:
        raise EmergencyImageBundleError("immutable build context file cannot be created") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _tree_digest(rows: Iterable[tuple[str, str, int]]) -> str:
    digest = hashlib.sha256()
    for relative, content_digest, size in sorted(rows):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content_digest.encode("ascii"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


@dataclasses.dataclass(frozen=True)
class InputFile:
    relative: PurePosixPath
    size: int
    device: int
    inode: int
    mtime_ns: int


def _snapshot_input_tree(*, root: Path, label: str, maximum_bytes: int, require_wheels: bool) -> list[InputFile]:
    root = _require_absolute(root, label=label)
    _require_no_symlink_ancestors(root, label=label)
    try:
        root_meta = root.lstat()
    except OSError as exc:
        raise EmergencyImageBundleError(f"{label} cannot be inspected") from exc
    if (
        not stat.S_ISDIR(root_meta.st_mode)
        or stat.S_ISLNK(root_meta.st_mode)
        or root_meta.st_uid != os.geteuid()
        or stat.S_IMODE(root_meta.st_mode) & 0o022
    ):
        _fail(f"{label} is not an owner-controlled directory")
    files: list[InputFile] = []
    total = 0
    for current, directories, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories.sort()
        names.sort()
        for name in directories:
            candidate = current_path / name
            metadata = candidate.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                _fail(f"{label} contains an unsafe directory")
        for name in names:
            candidate = current_path / name
            metadata = candidate.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o022
                or metadata.st_size < 0
            ):
                _fail(f"{label} contains an unsafe file")
            relative = _safe_relative(candidate.relative_to(root).as_posix(), label=label)
            files.append(
                InputFile(
                    relative=relative,
                    size=metadata.st_size,
                    device=metadata.st_dev,
                    inode=metadata.st_ino,
                    mtime_ns=metadata.st_mtime_ns,
                )
            )
            total += metadata.st_size
            if len(files) > MAX_INPUT_FILES or total > maximum_bytes:
                _fail(f"{label} exceeds its fixed immutable input bound")
    if not files:
        _fail(f"{label} is empty")
    names = {item.relative.as_posix() for item in files}
    if label == "frontend distribution" and "index.html" not in names:
        _fail("frontend distribution omits index.html")
    if require_wheels and not any(name.endswith(".whl") for name in names):
        _fail("wheelhouse omits local wheels; refusing an implicit package download")
    return sorted(files, key=lambda item: item.relative.as_posix())


def _copy_input_file(*, source: Path, destination: Path, expected: InputFile, label: str) -> tuple[str, int]:
    descriptor_in: int | None = None
    descriptor_out: int | None = None
    before = source / expected.relative
    try:
        descriptor_in = os.open(before, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        source_meta = os.fstat(descriptor_in)
        if (
            not stat.S_ISREG(source_meta.st_mode)
            or source_meta.st_uid != os.geteuid()
            or stat.S_IMODE(source_meta.st_mode) & 0o022
            or (source_meta.st_dev, source_meta.st_ino, source_meta.st_size, source_meta.st_mtime_ns)
            != (expected.device, expected.inode, expected.size, expected.mtime_ns)
        ):
            _fail(f"{label} changed while its immutable context was being copied")
        descriptor_out = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o444,
        )
        digest = hashlib.sha256()
        copied = 0
        while True:
            chunk = os.read(descriptor_in, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            copied += len(chunk)
            view = memoryview(chunk)
            offset = 0
            while offset < len(view):
                written = os.write(descriptor_out, view[offset:])
                if written <= 0:
                    raise OSError("context copy write made no progress")
                offset += written
        os.fchmod(descriptor_out, 0o444)
        os.fsync(descriptor_out)
        after = before.lstat()
        if (
            copied != expected.size
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (expected.device, expected.inode, expected.size, expected.mtime_ns)
        ):
            _fail(f"{label} changed while its immutable context was being copied")
        return digest.hexdigest(), copied
    except EmergencyImageBundleError:
        raise
    except (FileExistsError, OSError) as exc:
        raise EmergencyImageBundleError(f"{label} cannot be copied into the immutable context") from exc
    finally:
        if descriptor_out is not None:
            os.close(descriptor_out)
        if descriptor_in is not None:
            os.close(descriptor_in)


def _copy_supplied_tree(
    *,
    source: Path,
    context: Path,
    destination_name: str,
    label: str,
    maximum_bytes: int,
    require_wheels: bool,
) -> TreeIdentity:
    snapshot = _snapshot_input_tree(
        root=source, label=label, maximum_bytes=maximum_bytes, require_wheels=require_wheels
    )
    destination_root = _ensure_context_parent(context, _safe_relative(destination_name + "/.marker", label="context destination")).parent
    destination_root.mkdir(mode=0o555, exist_ok=True)
    destination_root.chmod(0o555)
    rows: list[tuple[str, str, int]] = []
    for item in snapshot:
        relative = _safe_relative(f"{destination_name}/{item.relative.as_posix()}", label="context input path")
        destination = _ensure_context_parent(context, relative)
        digest, size = _copy_input_file(source=source, destination=destination, expected=item, label=label)
        rows.append((relative.as_posix(), digest, size))
    return TreeIdentity(sha256=_tree_digest(rows), bytes=sum(size for _, _, size in rows), files=len(rows))


def _prepare_immutable_context(
    *,
    repo: Path,
    context_output: Path,
    frontend_dist: Path,
    wheelhouse: Path,
    runner: Runner,
) -> dict[str, TreeIdentity]:
    blobs = _git_blobs(repo=repo, runner=runner)
    _validate_dockerfile_contract(repo=repo, runner=runner)
    _make_context_directory(context_output)
    rows: list[tuple[str, str, int]] = []
    total = 0
    for blob in blobs:
        relative = _safe_relative(blob.path, label="immutable Git context path")
        payload = _read_head_blob(repo=repo, relative=blob.path, runner=runner)
        total += len(payload)
        if total > MAX_CONTEXT_BYTES:
            _fail("immutable Git context exceeds its fixed size bound")
        target = _ensure_context_parent(context_output, relative)
        _write_context_file(target, payload)
        rows.append((relative.as_posix(), hashlib.sha256(payload).hexdigest(), len(payload)))
    git_identity = TreeIdentity(sha256=_tree_digest(rows), bytes=total, files=len(rows))
    frontend_identity = _copy_supplied_tree(
        source=frontend_dist,
        context=context_output,
        destination_name=CONTEXT_FRONTEND_DIRECTORY,
        label="frontend distribution",
        maximum_bytes=MAX_FRONTEND_BYTES,
        require_wheels=False,
    )
    wheelhouse_identity = _copy_supplied_tree(
        source=wheelhouse,
        context=context_output,
        destination_name=CONTEXT_WHEELHOUSE_DIRECTORY,
        label="wheelhouse",
        maximum_bytes=MAX_WHEELHOUSE_BYTES,
        require_wheels=True,
    )
    if git_identity.bytes + frontend_identity.bytes + wheelhouse_identity.bytes > MAX_CONTEXT_BYTES:
        _fail("immutable build context exceeds its fixed size bound")
    return {"git": git_identity, "frontend": frontend_identity, "wheelhouse": wheelhouse_identity}


def _inspect_local_image(*, image: str, label: str, runner: Runner) -> ImageIdentity:
    image = _validate_image_reference(image, label=label)
    result = _run(
        ["docker", "image", "inspect", image, "--format", "{{json .}}"], runner=runner, timeout=30
    )
    if getattr(result, "returncode", 1) != 0:
        _fail(f"required local {label} image is unavailable")
    try:
        payload = json.loads(_as_text(getattr(result, "stdout", "")).strip())
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EmergencyImageBundleError(f"local {label} image inspection is malformed") from exc
    if not isinstance(payload, dict):
        _fail(f"local {label} image inspection is malformed")
    image_id = payload.get("Id")
    raw_tags = payload.get("RepoTags")
    if IMAGE_ID_RE.fullmatch(str(image_id)) is None:
        _fail(f"local {label} image has an invalid image ID")
    if raw_tags is None:
        tags: tuple[str, ...] = ()
    elif isinstance(raw_tags, list) and all(isinstance(item, str) for item in raw_tags):
        tags = tuple(sorted(set(raw_tags)))
    else:
        _fail(f"local {label} image tags are malformed")
    if any(any(marker in tag.lower() for marker in FORBIDDEN_TAG_MARKERS) for tag in tags):
        _fail(f"local {label} image carries a forbidden staging/three-site tag")
    return ImageIdentity(reference=image, image_id=str(image_id), repo_tags=tags)


def _assert_tag_absent(*, tag: str, label: str, runner: Runner) -> None:
    result = _run(
        ["docker", "image", "ls", "--no-trunc", "--format", "{{.Repository}}:{{.Tag}} {{.ID}}", tag],
        runner=runner,
        timeout=30,
    )
    if getattr(result, "returncode", 1) != 0:
        _fail(f"cannot verify absence of {label} image tag")
    if _as_text(getattr(result, "stdout", "")).strip():
        _fail(f"refusing to overwrite existing {label} image tag")


def _build_application_image(*, context: Path, patch_sha: str, runner: Runner) -> str:
    tag = f"{APP_REPOSITORY}:{patch_sha}"
    command = [
        "docker",
        "build",
        "--pull=false",
        "--file",
        str(context / "Dockerfile"),
        "--tag",
        tag,
        "--label",
        f"org.opencontainers.image.revision={patch_sha}",
        "--label",
        f"org.goldtrade.emergency.base-revision={SOURCE_RELEASE_SHA}",
        "--label",
        "org.goldtrade.emergency.scope=ir-standalone",
        "--label",
        "org.goldtrade.emergency.auth=webapp-initdata-and-local-sms-otp",
        "--build-arg",
        f"FRONTEND_DIST_DIR={CONTEXT_FRONTEND_DIRECTORY}",
        str(context),
    ]
    _run_success(command, runner=runner, purpose="local Emergency application image build", timeout=3600)
    return tag


def _verify_application_image(*, tag: str, patch_sha: str, runner: Runner) -> ImageIdentity:
    identity = _inspect_local_image(image=tag, label="Emergency application", runner=runner)
    result = _run_success(
        ["docker", "image", "inspect", tag, "--format", "{{json .}}"],
        runner=runner,
        purpose="Emergency application provenance inspection",
    )
    try:
        payload = json.loads(_as_text(getattr(result, "stdout", "")).strip())
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EmergencyImageBundleError("Emergency application provenance inspection is malformed") from exc
    failures = app_provenance.verify_payload(
        payload=payload,
        source_release_sha=SOURCE_RELEASE_SHA,
        emergency_patch_sha=patch_sha,
    )
    if failures:
        _fail("Emergency application image fails its provenance/tag contract")
    return identity


def _base_target_tag(*, repository: str, image_id: str) -> str:
    if IMAGE_ID_RE.fullmatch(image_id) is None:
        _fail("local base image ID is invalid")
    return f"{repository}:sha256-{image_id.partition(':')[2]}"


def _retag_exact_local_image(
    *,
    source: ImageIdentity,
    target_tag: str,
    label: str,
    runner: Runner,
) -> ImageIdentity:
    _assert_tag_absent(tag=target_tag, label=label, runner=runner)
    _run_success(
        ["docker", "image", "tag", source.image_id, target_tag],
        runner=runner,
        purpose=f"local {label} image retag",
    )
    tagged = _inspect_local_image(image=target_tag, label=label, runner=runner)
    if tagged.image_id != source.image_id or target_tag not in tagged.repo_tags:
        _fail(f"local {label} image retag does not preserve the exact source image ID")
    return tagged


def _save_images_create_only(*, output: Path, tags: Sequence[str], runner: Runner) -> None:
    _require_absent(output, label="Emergency image bundle")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            result = runner(
                ["docker", "image", "save", *tags],
                check=False,
                stdout=descriptor,
                stderr=subprocess.PIPE,
                text=False,
                timeout=3600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise EmergencyImageBundleError("local Emergency image bundle save could not be executed") from exc
        if getattr(result, "returncode", 1) != 0:
            _fail("local Emergency image bundle save failed")
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise EmergencyImageBundleError("refusing to overwrite existing Emergency image bundle") from exc
    except EmergencyImageBundleError:
        raise
    except OSError as exc:
        raise EmergencyImageBundleError("Emergency image bundle cannot be created") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _hash_regular_file(path: Path, *, label: str, maximum_bytes: int) -> tuple[str, int]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise EmergencyImageBundleError(f"{label} cannot be inspected") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or not 1 <= metadata.st_size <= maximum_bytes
    ):
        _fail(f"{label} is not a bounded owner-only regular file")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (metadata.st_dev, metadata.st_ino, metadata.st_size):
            _fail(f"{label} changed while being hashed")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        if total != metadata.st_size:
            _fail(f"{label} changed while being hashed")
        return digest.hexdigest(), total
    except EmergencyImageBundleError:
        raise
    except OSError as exc:
        raise EmergencyImageBundleError(f"{label} cannot be hashed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _verify_saved_bundle(
    *, output: Path, app: ImageIdentity, postgres: ImageIdentity, redis: ImageIdentity, patch_sha: str
) -> None:
    expected = {
        app.reference: app.image_id,
        postgres.reference: postgres.image_id,
        redis.reference: redis.image_id,
    }
    try:
        with tarfile.open(output, mode="r:") as archive:
            try:
                member = archive.getmember("manifest.json")
            except KeyError as exc:
                raise EmergencyImageBundleError("saved Emergency image bundle omits manifest.json") from exc
            if not member.isreg() or member.issym() or member.islnk() or not 1 <= member.size <= MAX_METADATA_BYTES:
                _fail("saved Emergency image bundle manifest is unsafe")
            handle = archive.extractfile(member)
            if handle is None:
                _fail("saved Emergency image bundle manifest is unavailable")
            try:
                values = json.loads(handle.read().decode("utf-8"))
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                raise EmergencyImageBundleError("saved Emergency image bundle manifest is invalid") from exc
            if not isinstance(values, list) or len(values) != 3:
                _fail("saved Emergency image bundle has an unexpected image count")
            seen: set[str] = set()
            for value in values:
                if not isinstance(value, dict) or set(value) != {"Config", "RepoTags", "Layers"}:
                    _fail("saved Emergency image bundle manifest entry is unsupported")
                config_name = value.get("Config")
                tags = value.get("RepoTags")
                if (
                    not isinstance(config_name, str)
                    or CONFIG_NAME_RE.fullmatch(config_name) is None
                    or not isinstance(tags, list)
                    or len(tags) != 1
                    or not isinstance(tags[0], str)
                ):
                    _fail("saved Emergency image bundle metadata is invalid")
                tag = tags[0]
                if tag not in expected or tag in seen or any(marker in tag.lower() for marker in FORBIDDEN_TAG_MARKERS):
                    _fail("saved Emergency image bundle has a forbidden or unexpected tag")
                if f"sha256:{config_name[:-5]}" != expected[tag]:
                    _fail("saved Emergency image bundle does not preserve the inspected image ID")
                try:
                    config_member = archive.getmember(config_name)
                except KeyError as exc:
                    raise EmergencyImageBundleError("saved Emergency image bundle omits an image config") from exc
                if not config_member.isreg() or config_member.issym() or config_member.islnk() or config_member.size > MAX_METADATA_BYTES:
                    _fail("saved Emergency image bundle image config is unsafe")
                config_handle = archive.extractfile(config_member)
                if config_handle is None:
                    _fail("saved Emergency image bundle image config is unavailable")
                try:
                    config = json.loads(config_handle.read().decode("utf-8"))
                except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                    raise EmergencyImageBundleError("saved Emergency image bundle image config is invalid") from exc
                if not isinstance(config, dict):
                    _fail("saved Emergency image bundle image config is invalid")
                if tag == app.reference:
                    failures = app_provenance.verify_payload(
                        payload={"Config": config.get("config"), "RepoTags": [tag]},
                        source_release_sha=SOURCE_RELEASE_SHA,
                        emergency_patch_sha=patch_sha,
                    )
                    if failures:
                        _fail("saved Emergency application image fails its provenance/tag contract")
                seen.add(tag)
            if seen != set(expected):
                _fail("saved Emergency image bundle is incomplete")
    except EmergencyImageBundleError:
        raise
    except (tarfile.TarError, OSError) as exc:
        raise EmergencyImageBundleError("saved Emergency image bundle is invalid") from exc


def build_bundle(
    *,
    repo: Path,
    frontend_dist: Path,
    wheelhouse: Path,
    postgres_image: str,
    redis_image: str,
    output: Path,
    receipt_output: Path,
    context_output: Path,
    runner: Runner = subprocess.run,
) -> BundleResult:
    """Build, verify, retag, save, and receipt one isolated local bundle."""

    repo = _require_absolute(repo, label="repository")
    frontend_dist = _require_absolute(frontend_dist, label="frontend distribution")
    wheelhouse = _require_absolute(wheelhouse, label="wheelhouse")
    output = _require_absolute(output, label="Emergency image bundle")
    receipt_output = _require_absolute(receipt_output, label="Emergency image bundle receipt")
    context_output = _require_absolute(context_output, label="immutable build context")
    patch_sha = _assert_clean_checkout(repo=repo, runner=runner)
    _validate_dockerfile_contract(repo=repo, runner=runner)
    if len({output, receipt_output, context_output}) != 3:
        _fail("bundle, receipt, and immutable context outputs must be distinct")
    _require_absent(output, label="Emergency image bundle")
    _require_absent(receipt_output, label="Emergency image bundle receipt")
    _require_absent(context_output, label="immutable build context")
    _snapshot_input_tree(
        root=frontend_dist,
        label="frontend distribution",
        maximum_bytes=MAX_FRONTEND_BYTES,
        require_wheels=False,
    )
    _snapshot_input_tree(
        root=wheelhouse,
        label="wheelhouse",
        maximum_bytes=MAX_WHEELHOUSE_BYTES,
        require_wheels=True,
    )
    # All image availability checks happen before Docker can build or retag.
    _inspect_local_image(image=PYTHON_BASE_IMAGE, label="Python base", runner=runner)
    postgres_source = _inspect_local_image(image=postgres_image, label="PostgreSQL source", runner=runner)
    redis_source = _inspect_local_image(image=redis_image, label="Redis source", runner=runner)
    app_tag = f"{APP_REPOSITORY}:{patch_sha}"
    postgres_tag = _base_target_tag(repository=POSTGRES_REPOSITORY, image_id=postgres_source.image_id)
    redis_tag = _base_target_tag(repository=REDIS_REPOSITORY, image_id=redis_source.image_id)
    _assert_tag_absent(tag=app_tag, label="Emergency application", runner=runner)
    _assert_tag_absent(tag=postgres_tag, label="Emergency PostgreSQL", runner=runner)
    _assert_tag_absent(tag=redis_tag, label="Emergency Redis", runner=runner)
    identities = _prepare_immutable_context(
        repo=repo,
        context_output=context_output,
        frontend_dist=frontend_dist,
        wheelhouse=wheelhouse,
        runner=runner,
    )
    _build_application_image(context=context_output, patch_sha=patch_sha, runner=runner)
    app = _verify_application_image(tag=app_tag, patch_sha=patch_sha, runner=runner)
    postgres = _retag_exact_local_image(
        source=postgres_source, target_tag=postgres_tag, label="Emergency PostgreSQL", runner=runner
    )
    redis = _retag_exact_local_image(
        source=redis_source, target_tag=redis_tag, label="Emergency Redis", runner=runner
    )
    _save_images_create_only(output=output, tags=(app.reference, postgres.reference, redis.reference), runner=runner)
    bundle_sha256, bundle_bytes = _hash_regular_file(
        output, label="saved Emergency image bundle", maximum_bytes=MAX_BUNDLE_BYTES
    )
    _verify_saved_bundle(output=output, app=app, postgres=postgres, redis=redis, patch_sha=patch_sha)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "built-local-only",
        "source_release_sha": SOURCE_RELEASE_SHA,
        "emergency_patch_sha": patch_sha,
        "build_context": {
            "path": str(context_output),
            "git": dataclasses.asdict(identities["git"]),
            "frontend": dataclasses.asdict(identities["frontend"]),
            "wheelhouse": dataclasses.asdict(identities["wheelhouse"]),
        },
        "images": {
            "app": {"tag": app.reference, "image_id": app.image_id},
            "postgres": {
                "source_image_id": postgres_source.image_id,
                "tag": postgres.reference,
                "image_id": postgres.image_id,
            },
            "redis": {
                "source_image_id": redis_source.image_id,
                "tag": redis.reference,
                "image_id": redis.image_id,
            },
        },
        "image_bundle": {"path": str(output), "sha256": bundle_sha256, "bytes": bundle_bytes},
    }
    receipt_payload = _canonical_json(receipt)
    _write_create_only(receipt_output, receipt_payload, label="Emergency image bundle receipt")
    return BundleResult(receipt=receipt, receipt_sha256=hashlib.sha256(receipt_payload).hexdigest())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--frontend-dist", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--postgres-image", required=True)
    parser.add_argument("--redis-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt-output", type=Path, required=True)
    parser.add_argument(
        "--context-output",
        type=Path,
        help="new immutable build context directory; defaults beside --output",
    )
    args = parser.parse_args(argv)
    output = args.output
    wheelhouse = args.wheelhouse or args.repo / "pip_packages"
    context_output = args.context_output if args.context_output else output.parent / f"{output.name}.context"
    try:
        result = build_bundle(
            repo=args.repo,
            frontend_dist=args.frontend_dist,
            wheelhouse=wheelhouse,
            postgres_image=args.postgres_image,
            redis_image=args.redis_image,
            output=output,
            receipt_output=args.receipt_output,
            context_output=context_output,
        )
    except EmergencyImageBundleError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "status": "built-local-only",
                "receipt": str(args.receipt_output),
                "receipt_sha256": result.receipt_sha256,
                "image_bundle": result.receipt["image_bundle"],
                "images": result.receipt["images"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
