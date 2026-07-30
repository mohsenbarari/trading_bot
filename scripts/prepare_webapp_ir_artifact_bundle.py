#!/usr/bin/env python3
"""Prepare detached local inputs for immutable WA-IR artifact staging.

This is deliberately a local preparation primitive.  It creates an exact Git
bundle and a Docker image archive from images that already exist on the local
daemon, then emits root-only artifacts and signed-manifest-compatible binding
arguments for ``manage_webapp_ir_artifact_stage.py``.  It never contacts S3,
uses SSH, builds, pulls, loads, or runs Docker images, or changes a container.

The Git-less source-tree alternative is intentionally not implemented here.
An arbitrary deployed directory cannot prove that it corresponds to a Git
release SHA.  A future implementation must first verify a source-signed,
root-only release-tree descriptor that binds the release SHA to a canonical
per-file tree hash, re-hash every accepted regular file, create a deterministic
archive in a fresh directory, and bind the archive digest to that descriptor.
Until such a descriptor is available, this command fails closed rather than
archiving a live runtime that lacks Git metadata.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


def _load_image_archive_contract() -> Any:
    """Load the pure tag contract from the exact sibling source file."""

    module_path = Path(__file__).with_name("webapp_ir_image_archive_contract.py")
    spec = importlib.util.spec_from_file_location("_wa_ir_image_archive_contract", module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError("cannot load WA-IR image archive contract")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


image_contract = _load_image_archive_contract()


PREPARATION_SCHEMA = "gold-trade-wa-ir-artifact-preparation-v1"
IMAGE_MANIFEST_SCHEMA = "gold-trade-wa-ir-image-manifest-v1"
GITLESS_TREE_CONTRACT_SCHEMA = "gold-trade-wa-ir-gitless-release-tree-contract-v1"

DEFAULT_MAXIMUM_ARTIFACT_BYTES = 20 * 1024 * 1024 * 1024
MAXIMUM_ARTIFACT_BYTES = 100 * 1024 * 1024 * 1024
CAPACITY_MARGIN_BYTES = 64 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 900

RELEASE_SHA_RE = re.compile(r"^[a-f0-9]{40,64}$")
GIT_OBJECT_ID_RE = re.compile(r"^[a-f0-9]{40,64}$")
PREPARATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
IMAGE_ID_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
IMAGE_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,511}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
DOCKER_CONFIG_MEMBER_RE = re.compile(r"^[a-f0-9]{64}\.json$")

# Docker ``save`` archives are an input boundary when they return from a
# source host.  Keep every control-plane structure deliberately small; layer
# bytes themselves remain bounded by the surrounding artifact contract.
MAX_DOCKER_ARCHIVE_MEMBERS = 4096
MAX_DOCKER_ARCHIVE_IMAGES = 32
MAX_DOCKER_ARCHIVE_TAGS_PER_IMAGE = 128
MAX_DOCKER_ARCHIVE_LAYERS_PER_IMAGE = 2048
MAX_DOCKER_ARCHIVE_MANIFEST_BYTES = 1024 * 1024
MAX_DOCKER_ARCHIVE_CONFIG_BYTES = 2 * 1024 * 1024
MAX_DOCKER_ARCHIVE_CONTROL_BYTES = 16 * 1024 * 1024


class ArtifactPreparationError(RuntimeError):
    """Raised when a detached artifact cannot be safely prepared."""


@dataclasses.dataclass(frozen=True)
class ImageSpecification:
    reference: str
    expected_id: str


@dataclasses.dataclass(frozen=True)
class PreparedImage:
    source_ref: str
    image_id: str
    repo_digests: tuple[str, ...]
    repo_tags: tuple[str, ...]
    size_bytes: int
    archive_tag: str | None = None

    def as_manifest_value(self) -> dict[str, Any]:
        if self.archive_tag is None:  # pragma: no cover - enforced by prepare_artifacts.
            raise ArtifactPreparationError("prepared image is missing its isolated archive tag")
        return {
            "archive_tag": self.archive_tag,
            "image_id": self.image_id,
            "repo_digests": list(self.repo_digests),
            "repo_tags": list(self.repo_tags),
            "size_bytes": self.size_bytes,
            "source_ref": self.source_ref,
        }


@dataclasses.dataclass(frozen=True)
class DockerArchiveEntry:
    config_name: str
    image_id: str
    repo_tags: tuple[str, ...]
    layers: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class DockerArchiveInspection:
    entries: tuple[DockerArchiveEntry, ...]
    member_names: frozenset[str]
    regular_member_names: frozenset[str]


class _DockerArchiveTarInfo(tarfile.TarInfo):
    """Reject tar extensions before ``tarfile`` can buffer their payloads."""

    def _proc_member(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        if self.type in {
            tarfile.GNUTYPE_LONGNAME,
            tarfile.GNUTYPE_LONGLINK,
            tarfile.GNUTYPE_SPARSE,
            tarfile.XHDTYPE,
            tarfile.XGLTYPE,
            tarfile.SOLARIS_XHDTYPE,
        }:
            raise tarfile.ReadError("Docker image archives must not contain extended tar headers")
        return super()._proc_member(archive)


CommandRunner = Callable[[Sequence[str], Path | None, int], subprocess.CompletedProcess[str]]


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def utc_iso(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise ArtifactPreparationError("timestamp must be timezone-aware")
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def generate_preparation_id(now: dt.datetime | None = None) -> str:
    value = now or utc_now()
    return value.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(12)


def require_id(value: object, *, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ArtifactPreparationError(f"{field} has an unsafe format")
    return value


def require_positive_int(value: object, *, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise ArtifactPreparationError(f"{field} must be a positive bounded integer")
    return value


def require_nonnegative_int(value: object, *, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise ArtifactPreparationError(f"{field} must be a non-negative bounded integer")
    return value


def require_absolute_path(value: object, *, field: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise ArtifactPreparationError(f"{field} must be an absolute path")
    return value


def _lstat(path: Path, *, field: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise ArtifactPreparationError(f"{field} cannot be inspected") from exc


def require_root_only_directory(path: Path, *, field: str) -> Path:
    path = require_absolute_path(path, field=field)
    metadata = _lstat(path, field=field)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ArtifactPreparationError(f"{field} must be an existing non-symlink directory")
    if metadata.st_uid != 0 or metadata.st_mode & 0o077:
        raise ArtifactPreparationError(f"{field} must be root-only")
    return path.resolve(strict=True)


def require_root_owned_nonwritable_directory(path: Path, *, field: str) -> Path:
    path = require_absolute_path(path, field=field)
    metadata = _lstat(path, field=field)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ArtifactPreparationError(f"{field} must be an existing non-symlink directory")
    if metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise ArtifactPreparationError(f"{field} must be root-owned and not group/other writable")
    return path.resolve(strict=True)


def require_root_owned_source_repository(path: Path) -> Path:
    return require_root_owned_nonwritable_directory(path, field="source_repo")


def require_trusted_executable(path: Path, *, field: str) -> Path:
    path = require_absolute_path(path, field=field)
    metadata = _lstat(path, field=field)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ArtifactPreparationError(f"{field} must be a regular non-symlink executable")
    if metadata.st_uid != 0 or metadata.st_mode & 0o022 or not metadata.st_mode & 0o111:
        raise ArtifactPreparationError(f"{field} must be a trusted root-owned executable")
    return path.resolve(strict=True)


def require_private_regular_file(path: Path, *, field: str, maximum_bytes: int) -> Path:
    metadata = _lstat(path, field=field)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ArtifactPreparationError(f"{field} must be a regular non-symlink file")
    if metadata.st_uid != 0 or metadata.st_mode & 0o077:
        raise ArtifactPreparationError(f"{field} must be root-only")
    if metadata.st_size < 1 or metadata.st_size > maximum_bytes:
        raise ArtifactPreparationError(f"{field} exceeds its size bounds")
    return path.resolve(strict=True)


def _require_private_docker_archive_input(path: Path, *, field: str) -> Path:
    path = require_private_regular_file(
        path,
        field=field,
        maximum_bytes=MAXIMUM_ARTIFACT_BYTES,
    )
    if _lstat(path, field=field).st_nlink != 1:
        raise ArtifactPreparationError(f"{field} must not have additional hard links")
    return path


def _require_exact_private_docker_archive(
    path: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    field: str,
) -> Path:
    if not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(expected_sha256):
        raise ArtifactPreparationError(f"{field} expected SHA-256 is invalid")
    expected_bytes = require_positive_int(
        expected_bytes,
        field=f"{field} expected byte count",
        maximum=MAXIMUM_ARTIFACT_BYTES,
    )
    path = _require_private_docker_archive_input(path, field=field)
    try:
        observed_sha256, observed_bytes = sha256_file(path)
    except OSError as exc:
        raise ArtifactPreparationError(f"{field} cannot be hashed") from exc
    if (observed_sha256, observed_bytes) != (expected_sha256, expected_bytes):
        raise ArtifactPreparationError(f"{field} changed from its signed raw archive binding")
    return path


def default_command_runner(
    arguments: Sequence[str],
    cwd: Path | None,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    executable_name = Path(arguments[0]).name
    if executable_name == "git":
        # Do not let an inherited GIT_DIR, alternate object store, or global
        # user configuration redirect exact-release verification elsewhere.
        for key in list(environment):
            if key.startswith("GIT_"):
                environment.pop(key)
        environment["GIT_CONFIG_NOSYSTEM"] = "1"
        environment["HOME"] = "/nonexistent"
        environment["XDG_CONFIG_HOME"] = "/nonexistent"
    elif executable_name == "docker":
        # Artifact preparation is local-only.  A caller cannot accidentally
        # use a remote Docker context or a DOCKER_HOST inherited from a shell.
        for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
            environment.pop(key, None)
        environment["DOCKER_CONTEXT"] = "default"
    return subprocess.run(
        [str(item) for item in arguments],
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        encoding="utf-8",
        errors="strict",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
        umask=0o077,
        env=environment,
    )


def run_checked(
    arguments: Sequence[str],
    *,
    runner: CommandRunner,
    cwd: Path | None = None,
    timeout: int = COMMAND_TIMEOUT_SECONDS,
    label: str,
) -> str:
    try:
        result = runner(arguments, cwd, timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ArtifactPreparationError(f"{label} could not be started") from exc
    if result.returncode != 0:
        raise ArtifactPreparationError(f"{label} failed")
    if not isinstance(result.stdout, str):
        raise ArtifactPreparationError(f"{label} returned malformed output")
    return result.stdout.strip()


def require_release_sha(value: object) -> str:
    return require_id(value, field="release_sha", pattern=RELEASE_SHA_RE)


def _git_value(
    git_binary: Path,
    source_repo: Path,
    arguments: Sequence[str],
    *,
    runner: CommandRunner,
    label: str,
) -> str:
    return run_checked(
        [str(git_binary), "-C", str(source_repo), *arguments],
        runner=runner,
        label=label,
    )


def verify_exact_git_release(
    *,
    git_binary: Path,
    source_repo: Path,
    release_sha: str,
    runner: CommandRunner,
) -> str:
    git_dir = _git_value(
        git_binary,
        source_repo,
        ["rev-parse", "--absolute-git-dir"],
        runner=runner,
        label="Git source repository inspection",
    )
    if not git_dir:
        raise ArtifactPreparationError("source_repo is not a Git repository")
    require_root_owned_nonwritable_directory(Path(git_dir), field="source_repo Git directory")
    commit = _git_value(
        git_binary,
        source_repo,
        ["rev-parse", "--verify", release_sha + "^{commit}"],
        runner=runner,
        label="Git release commit verification",
    ).lower()
    if commit != release_sha:
        raise ArtifactPreparationError("source_repo does not contain the exact requested release commit")
    tree = _git_value(
        git_binary,
        source_repo,
        ["rev-parse", "--verify", release_sha + "^{tree}"],
        runner=runner,
        label="Git release tree verification",
    ).lower()
    if not GIT_OBJECT_ID_RE.fullmatch(tree):
        raise ArtifactPreparationError("Git release tree ID is invalid")
    return tree


def _bundle_head_ids(value: str) -> set[str]:
    lines = [line for line in value.splitlines() if line.strip()]
    if not lines:
        raise ArtifactPreparationError("Git bundle contains no heads")
    result: set[str] = set()
    for line in lines:
        parts = line.split()
        if len(parts) < 2 or not GIT_OBJECT_ID_RE.fullmatch(parts[0].lower()):
            raise ArtifactPreparationError("Git bundle head list is malformed")
        result.add(parts[0].lower())
    return result


def create_and_verify_git_bundle(
    *,
    git_binary: Path,
    source_repo: Path,
    workspace: Path,
    output_path: Path,
    release_sha: str,
    expected_tree: str,
    maximum_artifact_bytes: int,
    runner: CommandRunner,
) -> dict[str, Any]:
    if output_path.exists() or output_path.is_symlink():
        raise ArtifactPreparationError("refusing to overwrite the detached Git bundle output")
    with tempfile.TemporaryDirectory(prefix="wa-ir-bundle-verify-", dir=str(workspace)) as temporary:
        verifier_root = Path(temporary)
        verifier_root.chmod(0o700)
        bundle_source = verifier_root / "bundle-source.git"
        # ``git bundle create`` requires a ref tip, not a raw object ID.  Make
        # that temporary ref only in a fresh shared bare clone; the source
        # repository is never changed.
        run_checked(
            [
                str(git_binary),
                "clone",
                "--bare",
                "--shared",
                "--no-tags",
                str(source_repo),
                str(bundle_source),
            ],
            runner=runner,
            label="isolated Git bundle source initialization",
        )
        cloned_commit = _git_value(
            git_binary,
            bundle_source,
            ["rev-parse", "--verify", release_sha + "^{commit}"],
            runner=runner,
            label="isolated Git release verification",
        ).lower()
        cloned_tree = _git_value(
            git_binary,
            bundle_source,
            ["rev-parse", "--verify", release_sha + "^{tree}"],
            runner=runner,
            label="isolated Git tree verification",
        ).lower()
        if cloned_commit != release_sha or cloned_tree != expected_tree:
            raise ArtifactPreparationError("isolated Git source does not match the requested release tree")
        run_checked(
            [
                str(git_binary),
                "-C",
                str(bundle_source),
                "update-ref",
                "refs/heads/wa-ir-artifact-release",
                release_sha,
            ],
            runner=runner,
            label="isolated Git release ref creation",
        )
        run_checked(
            [
                str(git_binary),
                "-C",
                str(bundle_source),
                "bundle",
                "create",
                str(output_path),
                "refs/heads/wa-ir-artifact-release",
            ],
            runner=runner,
            label="Git bundle creation",
        )
        require_private_regular_file(
            output_path,
            field="detached Git bundle",
            maximum_bytes=maximum_artifact_bytes,
        )
        heads = _bundle_head_ids(
            run_checked(
                [str(git_binary), "bundle", "list-heads", str(output_path)],
                runner=runner,
                label="Git bundle head verification",
            )
        )
        if heads != {release_sha}:
            raise ArtifactPreparationError("Git bundle does not bind exactly the requested release commit")
        run_checked(
            [str(git_binary), "-C", str(bundle_source), "bundle", "verify", str(output_path)],
            runner=runner,
            label="Git bundle verification",
        )
        verifier_repo = verifier_root / "bundle.git"
        run_checked(
            [str(git_binary), "init", "--bare", str(verifier_repo)],
            runner=runner,
            label="fresh Git bundle verifier initialization",
        )
        run_checked(
            [str(git_binary), "-C", str(verifier_repo), "bundle", "unbundle", str(output_path)],
            runner=runner,
            label="fresh Git bundle object verification",
        )
        verified_commit = _git_value(
            git_binary,
            verifier_repo,
            ["rev-parse", "--verify", release_sha + "^{commit}"],
            runner=runner,
            label="fresh Git bundle release verification",
        ).lower()
        verified_tree = _git_value(
            git_binary,
            verifier_repo,
            ["rev-parse", "--verify", release_sha + "^{tree}"],
            runner=runner,
            label="fresh Git bundle tree verification",
        ).lower()
    if verified_commit != release_sha or verified_tree != expected_tree:
        raise ArtifactPreparationError("fresh Git bundle verification does not match the requested release tree")
    digest, size = sha256_file(output_path)
    return {
        "bytes": size,
        "git_commit": release_sha,
        "git_tree": expected_tree,
        "sha256": digest,
    }


def require_image_reference(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not IMAGE_REFERENCE_RE.fullmatch(value) or value.startswith("-"):
        raise ArtifactPreparationError(f"{field} has an unsafe image reference")
    if "@" in value:
        if value.count("@") != 1 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:-]{0,511}@sha256:[a-f0-9]{64}", value):
            raise ArtifactPreparationError(f"{field} must use a complete immutable repo digest")
    else:
        final_component = value.rsplit("/", 1)[-1]
        if ":" not in final_component:
            raise ArtifactPreparationError(f"{field} must include an explicit image tag")
    return value


def require_image_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not IMAGE_ID_RE.fullmatch(value):
        raise ArtifactPreparationError(f"{field} must be a full immutable Docker image ID")
    return value


def parse_image_specifications(values: Sequence[str]) -> list[ImageSpecification]:
    if not values:
        raise ArtifactPreparationError("at least one --image REF=IMAGE_ID is required")
    result: list[ImageSpecification] = []
    references: set[str] = set()
    image_ids: set[str] = set()
    for value in values:
        if value.count("=") != 1:
            raise ArtifactPreparationError("image must use REF=IMAGE_ID")
        reference, expected_id = value.split("=", 1)
        reference = require_image_reference(reference, field="image reference")
        expected_id = require_image_id(expected_id, field="expected image ID")
        if reference in references or expected_id in image_ids:
            raise ArtifactPreparationError("image references and expected image IDs must be unique")
        references.add(reference)
        image_ids.add(expected_id)
        result.append(ImageSpecification(reference=reference, expected_id=expected_id))
    return sorted(result, key=lambda item: item.reference)


def _normalized_image_values(value: object, *, field: str, digest: bool) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ArtifactPreparationError(f"{field} is malformed")
    normalized = tuple(sorted(require_image_reference(item, field=field) for item in value))
    if len(set(normalized)) != len(normalized):
        raise ArtifactPreparationError(f"{field} contains duplicate values")
    if digest and any("@sha256:" not in item for item in normalized):
        raise ArtifactPreparationError(f"{field} must contain immutable repo digests")
    if not digest and any("@" in item for item in normalized):
        raise ArtifactPreparationError(f"{field} must contain image tags")
    return normalized


def inspect_exact_images(
    *,
    docker_binary: Path,
    specifications: Sequence[ImageSpecification],
    runner: CommandRunner,
) -> list[PreparedImage]:
    images: list[PreparedImage] = []
    for specification in specifications:
        raw = run_checked(
            [str(docker_binary), "image", "inspect", "--format", "{{json .}}", specification.reference],
            runner=runner,
            label="Docker image inspection",
        )
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ArtifactPreparationError("Docker image inspection returned invalid JSON") from exc
        if not isinstance(value, Mapping):
            raise ArtifactPreparationError("Docker image inspection returned a malformed object")
        actual_id = require_image_id(value.get("Id"), field="Docker inspected image ID")
        if actual_id != specification.expected_id:
            raise ArtifactPreparationError("Docker image reference does not resolve to its expected immutable image ID")
        repo_tags = _normalized_image_values(value.get("RepoTags"), field="Docker inspected RepoTags", digest=False)
        repo_digests = _normalized_image_values(
            value.get("RepoDigests"),
            field="Docker inspected RepoDigests",
            digest=True,
        )
        size_bytes = require_nonnegative_int(
            value.get("Size"),
            field="Docker inspected image Size",
            maximum=MAXIMUM_ARTIFACT_BYTES,
        )
        if "@" in specification.reference:
            if specification.reference not in repo_digests:
                raise ArtifactPreparationError("Docker image digest reference is not bound by inspected RepoDigests")
        elif specification.reference not in repo_tags:
            raise ArtifactPreparationError("Docker image tag reference is not bound by inspected RepoTags")
        images.append(
            PreparedImage(
                source_ref=specification.reference,
                image_id=actual_id,
                repo_digests=repo_digests,
                repo_tags=repo_tags,
                size_bytes=size_bytes,
            )
        )
    return images


def preflight_artifact_capacity(
    *,
    workspace: Path,
    output_root: Path,
    maximum_artifact_bytes: int,
    images: Sequence[PreparedImage],
    disk_usage: Callable[[Path], Any] = shutil.disk_usage,
    stat_path: Callable[[Path], os.stat_result] = os.stat,
) -> dict[str, int]:
    """Reserve conservative local space before writing any detached artifact."""

    image_bytes = sum(image.size_bytes for image in images)
    output_required = maximum_artifact_bytes + image_bytes + CAPACITY_MARGIN_BYTES
    workspace_required = maximum_artifact_bytes + CAPACITY_MARGIN_BYTES
    try:
        workspace_free = int(disk_usage(workspace).free)
        output_free = int(disk_usage(output_root).free)
        same_filesystem = stat_path(workspace).st_dev == stat_path(output_root).st_dev
    except OSError as exc:
        raise ArtifactPreparationError("cannot inspect artifact workspace capacity") from exc
    if workspace_free < 0 or output_free < 0:
        raise ArtifactPreparationError("artifact workspace capacity is invalid")
    combined_required = workspace_required + output_required
    if (same_filesystem and min(workspace_free, output_free) < combined_required) or (
        not same_filesystem and (workspace_free < workspace_required or output_free < output_required)
    ):
        raise ArtifactPreparationError(
            "insufficient free space for detached artifacts; use explicit root-only directories on a dedicated volume"
        )
    return {
        "image_logical_bytes": image_bytes,
        "output_required_bytes": output_required,
        "output_free_bytes": output_free,
        "workspace_required_bytes": workspace_required,
        "workspace_free_bytes": workspace_free,
    }


def _safe_tar_member_name(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 1024
        and "\x00" not in value
        and not value.startswith("/")
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )


def bind_isolated_archive_tags(
    *,
    campaign_id: str,
    release_sha: str,
    images: Sequence[PreparedImage],
) -> list[PreparedImage]:
    """Bind every verified image to one deterministic, load-safe archive tag."""

    try:
        campaign_id = image_contract.require_campaign_id(campaign_id)
        release_sha = image_contract.require_release_sha(release_sha)
        result = [
            dataclasses.replace(
                image,
                archive_tag=image_contract.canonical_archive_tag(
                    campaign_id=campaign_id,
                    release_sha=release_sha,
                    image_id=image.image_id,
                ),
            )
            for image in images
        ]
    except image_contract.ImageArchiveContractError as exc:
        raise ArtifactPreparationError("cannot bind isolated Docker archive tags") from exc
    if len({image.archive_tag for image in result}) != len(result):  # pragma: no cover - full IDs make this impossible.
        raise ArtifactPreparationError("isolated Docker archive tags are not unique")
    return result


def _reject_duplicate_json_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON constant")


def _strict_json_loads(payload: bytes, *, field: str) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ArtifactPreparationError(f"{field} is invalid") from exc


def _read_archive_metadata_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    *,
    maximum_bytes: int,
    field: str,
) -> bytes:
    if not member.isreg() or member.issparse() or member.size < 1 or member.size > maximum_bytes:
        raise ArtifactPreparationError(f"{field} exceeds its fixed bounds")
    handle = archive.extractfile(member)
    if handle is None:
        raise ArtifactPreparationError(f"{field} cannot be read")
    try:
        payload = handle.read(maximum_bytes + 1)
    finally:
        handle.close()
    if len(payload) != member.size or len(payload) > maximum_bytes:
        raise ArtifactPreparationError(f"{field} cannot be read safely")
    return payload


def _validate_docker_archive_member(member: tarfile.TarInfo, *, member_names: set[str]) -> None:
    if not _safe_tar_member_name(member.name):
        raise ArtifactPreparationError("Docker image archive contains an unsafe member path")
    if member.name in member_names:
        raise ArtifactPreparationError("Docker image archive contains duplicate member paths")
    if len(member_names) >= MAX_DOCKER_ARCHIVE_MEMBERS:
        raise ArtifactPreparationError("Docker image archive contains too many members")
    member_names.add(member.name)
    if member.isdir():
        return
    if not member.isreg() or member.issparse() or member.size < 0:
        raise ArtifactPreparationError("Docker image archive contains unsupported member types")


def _parse_docker_archive_manifest(payload: bytes) -> list[Mapping[str, Any]]:
    manifest = _strict_json_loads(payload, field="Docker image archive manifest")
    if not isinstance(manifest, list) or not manifest or len(manifest) > MAX_DOCKER_ARCHIVE_IMAGES:
        raise ArtifactPreparationError("Docker image archive manifest is malformed")
    if not all(isinstance(entry, Mapping) for entry in manifest):
        raise ArtifactPreparationError("Docker image archive manifest entry is malformed")
    return manifest


def _validate_docker_archive_entries(
    manifest: Sequence[Mapping[str, Any]],
    *,
    member_names: set[str],
    regular_member_names: set[str],
    config_payloads: Mapping[str, bytes],
) -> tuple[DockerArchiveEntry, ...]:
    entries: list[DockerArchiveEntry] = []
    for entry in manifest:
        if set(entry) != {"Config", "RepoTags", "Layers"}:
            raise ArtifactPreparationError("Docker image archive manifest entry has unsupported fields")
        config_name = entry.get("Config")
        if not isinstance(config_name, str) or not DOCKER_CONFIG_MEMBER_RE.fullmatch(config_name):
            raise ArtifactPreparationError("Docker image archive config path is malformed")
        config_payload = config_payloads.get(config_name)
        if config_payload is None:
            raise ArtifactPreparationError("Docker image archive config path is absent")
        config = _strict_json_loads(config_payload, field="Docker image archive config")
        if not isinstance(config, Mapping):
            raise ArtifactPreparationError("Docker image archive config is malformed")
        image_id = require_image_id(
            "sha256:" + sha256_bytes(config_payload),
            field="Docker archive image ID",
        )
        if config_name != image_id.removeprefix("sha256:") + ".json":
            raise ArtifactPreparationError("Docker image archive config path is not bound to its image ID")
        repo_tags = entry.get("RepoTags")
        if repo_tags is None:
            validated_tags: tuple[str, ...] = ()
        elif (
            not isinstance(repo_tags, list)
            or len(repo_tags) > MAX_DOCKER_ARCHIVE_TAGS_PER_IMAGE
            or not all(isinstance(tag, str) for tag in repo_tags)
        ):
            raise ArtifactPreparationError("Docker image archive RepoTags are malformed")
        else:
            validated_tags = tuple(require_image_reference(tag, field="Docker archive RepoTag") for tag in repo_tags)
        if len(validated_tags) != len(set(validated_tags)):
            raise ArtifactPreparationError("Docker image archive contains duplicate RepoTags")
        layers = entry.get("Layers")
        if (
            not isinstance(layers, list)
            or len(layers) > MAX_DOCKER_ARCHIVE_LAYERS_PER_IMAGE
            or not all(isinstance(layer, str) for layer in layers)
        ):
            raise ArtifactPreparationError("Docker image archive Layers are malformed")
        validated_layers: list[str] = []
        for layer in layers:
            if (
                not _safe_tar_member_name(layer)
                or layer not in member_names
                or layer not in regular_member_names
                or layer in {"manifest.json", "repositories"}
                or layer in config_payloads
            ):
                raise ArtifactPreparationError("Docker image archive layer path is malformed")
            validated_layers.append(layer)
        if len(validated_layers) != len(set(validated_layers)):
            raise ArtifactPreparationError("Docker image archive contains duplicate layer paths")
        entries.append(
            DockerArchiveEntry(
                config_name=config_name,
                image_id=image_id,
                repo_tags=validated_tags,
                layers=tuple(validated_layers),
            )
        )
    return tuple(entries)


def inspect_docker_image_archive(*, path: Path) -> DockerArchiveInspection:
    """Read Docker archive control metadata with bounded, streaming parsing."""

    path = _require_private_docker_archive_input(path, field="Docker image archive")
    member_names: set[str] = set()
    regular_member_names: set[str] = set()
    config_payloads: dict[str, bytes] = {}
    manifest_payload: bytes | None = None
    control_bytes = 0
    try:
        with tarfile.open(path, "r|", tarinfo=_DockerArchiveTarInfo) as archive:
            while (member := archive.next()) is not None:
                _validate_docker_archive_member(member, member_names=member_names)
                if member.isreg():
                    regular_member_names.add(member.name)
                if member.name == "manifest.json":
                    manifest_payload = _read_archive_metadata_member(
                        archive,
                        member,
                        maximum_bytes=MAX_DOCKER_ARCHIVE_MANIFEST_BYTES,
                        field="Docker image archive manifest",
                    )
                    control_bytes += len(manifest_payload)
                elif DOCKER_CONFIG_MEMBER_RE.fullmatch(member.name):
                    if len(config_payloads) >= MAX_DOCKER_ARCHIVE_IMAGES:
                        raise ArtifactPreparationError("Docker image archive contains too many config members")
                    config_payload = _read_archive_metadata_member(
                        archive,
                        member,
                        maximum_bytes=MAX_DOCKER_ARCHIVE_CONFIG_BYTES,
                        field="Docker image archive config",
                    )
                    config_payloads[member.name] = config_payload
                    control_bytes += len(config_payload)
                if control_bytes > MAX_DOCKER_ARCHIVE_CONTROL_BYTES:
                    raise ArtifactPreparationError("Docker image archive control metadata exceeds its fixed bounds")
    except (OSError, tarfile.TarError) as exc:
        raise ArtifactPreparationError("Docker image archive cannot be validated") from exc
    if any(
        name in {"oci-layout", "index.json"} or name.startswith("blobs/")
        for name in member_names
    ):
        raise ArtifactPreparationError("Docker image archive uses an unsupported OCI layout")
    if manifest_payload is None:
        raise ArtifactPreparationError("Docker image archive does not contain manifest.json")
    manifest = _parse_docker_archive_manifest(manifest_payload)
    entries = _validate_docker_archive_entries(
        manifest,
        member_names=member_names,
        regular_member_names=regular_member_names,
        config_payloads=config_payloads,
    )
    if set(config_payloads) != {entry.config_name for entry in entries}:
        raise ArtifactPreparationError("Docker image archive contains an unreferenced config member")
    return DockerArchiveInspection(
        entries=entries,
        member_names=frozenset(member_names),
        regular_member_names=frozenset(regular_member_names),
    )


def _verify_docker_archive_inspection(
    inspection: DockerArchiveInspection,
    *,
    images: Sequence[PreparedImage],
    require_isolated_tags: bool,
    require_source_tags: bool,
) -> dict[str, Any]:
    if not images or len(images) > MAX_DOCKER_ARCHIVE_IMAGES:
        raise ArtifactPreparationError("Docker image archive has an unsafe expected image count")
    expected_ids = {image.image_id for image in images}
    if len(expected_ids) != len(images):
        raise ArtifactPreparationError("Docker image archive expected image IDs are not unique")
    expected_tags_by_image = {image.image_id: set(image.repo_tags) for image in images}
    expected_source_tags_by_image = {
        image.image_id: ({image.source_ref} if "@" not in image.source_ref else set())
        for image in images
    }
    image_by_id = {image.image_id: image for image in images}
    archive_ids: list[str] = []
    archive_tags: set[str] = set()
    for entry in inspection.entries:
        image_id = entry.image_id
        if image_id not in expected_ids:
            raise ArtifactPreparationError("Docker image archive contains an unverified image ID")
        entry_tags = set(entry.repo_tags)
        if require_isolated_tags:
            expected_tag = image_by_id[image_id].archive_tag
            if expected_tag is None:  # pragma: no cover - bind_isolated_archive_tags is mandatory before final verification.
                raise ArtifactPreparationError("Docker image archive is missing its isolated tag binding")
            if entry_tags != {expected_tag}:
                raise ArtifactPreparationError("Docker image archive contains shared or noncanonical image tags")
        elif require_source_tags:
            if not entry_tags:
                raise ArtifactPreparationError("Docker image archive does not retain every verified source image tag")
            if not entry_tags.issubset(expected_tags_by_image[image_id]):
                raise ArtifactPreparationError("Docker image archive contains an unverified image tag")
            if not expected_source_tags_by_image[image_id].issubset(entry_tags):
                raise ArtifactPreparationError("Docker image archive does not retain every verified source image tag")
        elif entry_tags:
            raise ArtifactPreparationError("untagged Docker image archive unexpectedly retains raw source tags")
        archive_ids.append(image_id)
        archive_tags.update(entry_tags)
    if len(archive_ids) != len(set(archive_ids)) or set(archive_ids) != expected_ids:
        raise ArtifactPreparationError("Docker image archive does not contain exactly the verified image IDs")
    return {
        "image_ids": sorted(archive_ids),
        "repo_tags": sorted(archive_tags),
    }


def verify_docker_image_archive(
    *,
    path: Path,
    images: Sequence[PreparedImage],
    require_isolated_tags: bool = False,
    require_source_tags: bool = True,
) -> dict[str, Any]:
    """Validate archive structure, config identities, and tag isolation only.

    The Docker config hash binds an image ID, but this pure file-level check
    intentionally does not assert that Docker can load every layer.  Only the
    legacy Docker-save layout is recognized; OCI layout control files fail
    closed rather than bypassing the rewritten tag manifest. Returned untagged
    source archives are accepted only with ``require_source_tags=False`` and
    only when their exact raw bytes are already signed by the source proof.
    Docker load remains a later isolated action.
    """

    inspection = inspect_docker_image_archive(path=path)
    if require_isolated_tags and "repositories" in inspection.member_names:
        raise ArtifactPreparationError("Docker image archive retains legacy repositories tag metadata")
    return _verify_docker_archive_inspection(
        inspection,
        images=images,
        require_isolated_tags=require_isolated_tags,
        require_source_tags=require_source_tags,
    )


def _open_new_private_binary(path: Path, *, field: str) -> Any:
    if path.exists() or path.is_symlink():
        raise ArtifactPreparationError(f"refusing to overwrite the detached {field} output")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise ArtifactPreparationError(f"refusing to overwrite the detached {field} output") from exc
    except OSError as exc:
        raise ArtifactPreparationError(f"cannot create the detached {field} output") from exc
    return os.fdopen(descriptor, "wb")


def rewrite_docker_image_archive_tags(
    *,
    raw_path: Path,
    output_path: Path,
    images: Sequence[PreparedImage],
    expected_raw_sha256: str,
    expected_raw_bytes: int,
    require_source_tags: bool = True,
) -> None:
    """Copy one SHA-pinned raw ``docker save`` archive with isolated tags only.

    ``docker save`` necessarily serializes the source reference it was given.
    Rewriting its manifest is safer than adding/removing tags on the shared
    source daemon: the final archive has no source tag and no legacy
    ``repositories`` metadata that another Docker version could interpret.
    This is a structural/tag isolation primitive, not a Docker-loadability
    claim.  The caller must supply the exact signed raw archive digest and
    byte count; that input is rechecked before and after the copy.
    """

    raw_path = _require_exact_private_docker_archive(
        raw_path,
        expected_sha256=expected_raw_sha256,
        expected_bytes=expected_raw_bytes,
        field="raw Docker image archive",
    )
    inspection = inspect_docker_image_archive(path=raw_path)
    _verify_docker_archive_inspection(
        inspection,
        images=images,
        require_isolated_tags=False,
        require_source_tags=require_source_tags,
    )
    expected_tags = {image.image_id: image.archive_tag for image in images}
    if any(tag is None for tag in expected_tags.values()):
        raise ArtifactPreparationError("isolated Docker archive tag bindings are absent")
    tags_by_config = {entry.config_name: expected_tags[entry.image_id] for entry in inspection.entries}
    _require_exact_private_docker_archive(
        raw_path,
        expected_sha256=expected_raw_sha256,
        expected_bytes=expected_raw_bytes,
        field="raw Docker image archive",
    )
    try:
        with tarfile.open(raw_path, "r|", tarinfo=_DockerArchiveTarInfo) as source, _open_new_private_binary(
            output_path,
            field="Docker image archive",
        ) as output:
            with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as destination:
                copied_names: set[str] = set()
                while (member := source.next()) is not None:
                    _validate_docker_archive_member(member, member_names=copied_names)
                    if member.name not in inspection.member_names:
                        raise ArtifactPreparationError("Docker image archive changed while being rewritten")
                    if member.name in {"manifest.json", "repositories"}:
                        continue
                    copied = copy.copy(member)
                    copied.pax_headers = {}
                    copied.uid = 0
                    copied.gid = 0
                    copied.uname = ""
                    copied.gname = ""
                    copied.mtime = 0
                    if copied.isdir():
                        destination.addfile(copied)
                        continue
                    payload = source.extractfile(member)
                    if payload is None:
                        raise ArtifactPreparationError("Docker image archive member cannot be read")
                    try:
                        destination.addfile(copied, payload)
                    finally:
                        payload.close()
                if copied_names != set(inspection.member_names):
                    raise ArtifactPreparationError("Docker image archive changed while being rewritten")
                rewritten_manifest = [
                    {"Config": entry.config_name, "Layers": list(entry.layers), "RepoTags": [tags_by_config[entry.config_name]]}
                    for entry in inspection.entries
                ]
                encoded_manifest = canonical_json_bytes(rewritten_manifest)
                manifest_info = tarfile.TarInfo("manifest.json")
                manifest_info.mode = 0o600
                manifest_info.uid = 0
                manifest_info.gid = 0
                manifest_info.mtime = 0
                manifest_info.size = len(encoded_manifest)
                destination.addfile(manifest_info, io.BytesIO(encoded_manifest))
            output.flush()
            os.fsync(output.fileno())
    except (OSError, tarfile.TarError) as exc:
        raise ArtifactPreparationError("Docker image archive cannot be rewritten safely") from exc
    _require_exact_private_docker_archive(
        raw_path,
        expected_sha256=expected_raw_sha256,
        expected_bytes=expected_raw_bytes,
        field="raw Docker image archive",
    )
    verify_docker_image_archive(path=output_path, images=images, require_isolated_tags=True)


def create_and_verify_docker_image_archive(
    *,
    docker_binary: Path,
    output_path: Path,
    workspace: Path,
    images: Sequence[PreparedImage],
    maximum_artifact_bytes: int,
    runner: CommandRunner,
) -> dict[str, Any]:
    workspace = require_root_only_directory(workspace, field="workspace")
    if any(image.archive_tag is None for image in images):
        raise ArtifactPreparationError("detached Docker image archive requires isolated tag bindings")
    with tempfile.TemporaryDirectory(prefix=".wa-ir-image-save-", dir=workspace) as temporary:
        raw_path = Path(temporary) / "raw-images.tar"
        # The raw archive is private, short-lived workspace data only.  It is
        # never staged or returned to the caller because it retains source tags.
        run_checked(
            [str(docker_binary), "image", "save", "--output", str(raw_path), *[image.source_ref for image in images]],
            runner=runner,
            label="Docker image archive creation",
        )
        require_private_regular_file(
            raw_path,
            field="temporary raw Docker image archive",
            maximum_bytes=maximum_artifact_bytes,
        )
        raw_sha256, raw_bytes = sha256_file(raw_path)
        rewrite_docker_image_archive_tags(
            raw_path=raw_path,
            output_path=output_path,
            images=images,
            expected_raw_sha256=raw_sha256,
            expected_raw_bytes=raw_bytes,
        )
    require_private_regular_file(
        output_path,
        field="detached Docker image archive",
        maximum_bytes=maximum_artifact_bytes,
    )
    digest, size = sha256_file(output_path)
    verified = verify_docker_image_archive(path=output_path, images=images, require_isolated_tags=True)
    if sha256_file(output_path) != (digest, size):
        raise ArtifactPreparationError("detached Docker image archive changed while being verified")
    return {"bytes": size, "sha256": digest, **verified}


def write_new_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise ArtifactPreparationError("refusing to overwrite a detached JSON artifact")
    encoded = canonical_json_bytes(payload) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(path), flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise ArtifactPreparationError("refusing to overwrite a detached JSON artifact") from exc
    except OSError as exc:
        raise ArtifactPreparationError("cannot create a detached JSON artifact") from exc


def candidate_directory(output_root: Path, *, release_sha: str, preparation_id: str) -> Path:
    return output_root / ("prepared-" + release_sha + "-" + preparation_id)


def _artifact_descriptor(name: str, path: Path, bindings: Mapping[str, str]) -> dict[str, Any]:
    digest, size = sha256_file(path)
    return {
        "bindings": dict(sorted(bindings.items())),
        "bytes": size,
        "name": name,
        "path": str(path),
        "sha256": digest,
    }


def _stage_arguments(artifacts: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    artifact_values: list[str] = []
    binding_values: list[str] = []
    for artifact in sorted(artifacts, key=lambda item: str(item["name"])):
        name = str(artifact["name"])
        artifact_values.append(name + "=" + str(artifact["path"]))
        bindings = artifact["bindings"]
        if not isinstance(bindings, Mapping):  # pragma: no cover - internal invariant.
            raise ArtifactPreparationError("artifact bindings are malformed")
        for key, value in sorted(bindings.items()):
            if not isinstance(key, str) or not isinstance(value, str):  # pragma: no cover - internal invariant.
                raise ArtifactPreparationError("artifact bindings are malformed")
            binding_values.append(name + "=" + key + "=" + value)
    return {"artifact": artifact_values, "artifact_binding": binding_values}


def gitless_release_tree_contract() -> dict[str, Any]:
    """Describe the intentionally separate, fail-closed Git-less release path."""

    return {
        "schema": GITLESS_TREE_CONTRACT_SCHEMA,
        "status": "not_implemented_fail_closed",
        "required_root_only_inputs": [
            "source-signed-release-tree-descriptor.json",
            "pinned-source-signing-public-key",
            "immutable-source-tree-directory",
        ],
        "descriptor_bindings": [
            "release_sha",
            "canonical_tree_sha256",
            "per-file path, mode, sha256, and byte count",
            "source signature over canonical descriptor bytes",
        ],
        "required_verification": [
            "reject symlinks, hardlinks, devices, unsafe paths, and unlisted files",
            "recompute every file digest and canonical tree hash before archive creation",
            "create deterministic archive only in a new root-only candidate directory",
            "re-read archive and bind its sha256 to the verified descriptor",
        ],
        "reason": "a mutable Git-less runtime directory cannot itself prove a Git release SHA",
    }


def prepare_artifacts(
    *,
    source_repo: Path,
    release_sha: str,
    workspace: Path,
    output_root: Path,
    image_specifications: Sequence[ImageSpecification],
    git_binary: Path,
    docker_binary: Path,
    preparation_id: str | None = None,
    campaign_id: str | None = None,
    maximum_artifact_bytes: int = DEFAULT_MAXIMUM_ARTIFACT_BYTES,
    now: dt.datetime | None = None,
    runner: CommandRunner = default_command_runner,
) -> dict[str, Any]:
    release_sha = require_release_sha(release_sha)
    preparation_id = require_id(
        preparation_id or generate_preparation_id(now),
        field="preparation_id",
        pattern=PREPARATION_ID_RE,
    )
    try:
        campaign_id = image_contract.require_campaign_id(campaign_id or preparation_id)
    except image_contract.ImageArchiveContractError as exc:
        raise ArtifactPreparationError("campaign_id has an unsafe format") from exc
    maximum_artifact_bytes = require_positive_int(
        maximum_artifact_bytes,
        field="maximum_artifact_bytes",
        maximum=MAXIMUM_ARTIFACT_BYTES,
    )
    source_repo = require_root_owned_source_repository(source_repo)
    workspace = require_root_only_directory(workspace, field="workspace")
    output_root = require_root_only_directory(output_root, field="output_root")
    git_binary = require_trusted_executable(git_binary, field="git_binary")
    docker_binary = require_trusted_executable(docker_binary, field="docker_binary")
    specifications = list(image_specifications)
    if not specifications:
        raise ArtifactPreparationError("at least one image specification is required")
    if len({item.reference for item in specifications}) != len(specifications):
        raise ArtifactPreparationError("image specifications must have unique references")
    if len({item.expected_id for item in specifications}) != len(specifications):
        raise ArtifactPreparationError("image specifications must have unique image IDs")
    expected_tree = verify_exact_git_release(
        git_binary=git_binary,
        source_repo=source_repo,
        release_sha=release_sha,
        runner=runner,
    )
    images = bind_isolated_archive_tags(
        campaign_id=campaign_id,
        release_sha=release_sha,
        images=inspect_exact_images(
            docker_binary=docker_binary,
            specifications=sorted(specifications, key=lambda item: item.reference),
            runner=runner,
        ),
    )
    capacity_preflight = preflight_artifact_capacity(
        workspace=workspace,
        output_root=output_root,
        maximum_artifact_bytes=maximum_artifact_bytes,
        images=images,
    )
    target = candidate_directory(output_root, release_sha=release_sha, preparation_id=preparation_id)
    if target.exists() or target.is_symlink():
        raise ArtifactPreparationError("refusing to overwrite an existing detached preparation directory")
    try:
        target.mkdir(mode=0o700)
    except OSError as exc:
        raise ArtifactPreparationError("cannot create a detached preparation directory") from exc
    # Do not delete this fresh candidate on a later validation failure.  It is
    # deliberately retained without a success receipt for forensic evidence;
    # retries must use a different preparation ID rather than overwrite it.
    bundle_path = target / "release.bundle"
    bundle = create_and_verify_git_bundle(
        git_binary=git_binary,
        source_repo=source_repo,
        workspace=workspace,
        output_path=bundle_path,
        release_sha=release_sha,
        expected_tree=expected_tree,
        maximum_artifact_bytes=maximum_artifact_bytes,
        runner=runner,
    )
    image_path = target / "images.tar"
    image_archive = create_and_verify_docker_image_archive(
        docker_binary=docker_binary,
        output_path=image_path,
        workspace=workspace,
        images=images,
        maximum_artifact_bytes=maximum_artifact_bytes,
        runner=runner,
    )
    image_values = [item.as_manifest_value() for item in images]
    image_set_sha256 = sha256_bytes(canonical_json_bytes(image_values))
    image_ids_sha256 = sha256_bytes(canonical_json_bytes([item.image_id for item in images]))
    image_manifest_path = target / "image-manifest.json"
    write_new_private_json(
        image_manifest_path,
        {
            "archive": image_archive,
            "campaign_id": campaign_id,
            "image_set_sha256": image_set_sha256,
            "images": image_values,
            "release_sha": release_sha,
            "schema": IMAGE_MANIFEST_SCHEMA,
            "status": "prepared",
        },
    )
    require_private_regular_file(
        image_manifest_path,
        field="detached image manifest",
        maximum_bytes=1024 * 1024,
    )
    artifacts = [
        _artifact_descriptor(
            "image-bundle",
            image_path,
            {
                "artifact_sha256": image_archive["sha256"],
                "image_count": str(len(images)),
                "image_ids_sha256": image_ids_sha256,
                "image_manifest_sha256": sha256_file(image_manifest_path)[0],
                "image_set_sha256": image_set_sha256,
                "release_sha": release_sha,
            },
        ),
        _artifact_descriptor(
            "image-manifest",
            image_manifest_path,
            {
                "artifact_sha256": sha256_file(image_manifest_path)[0],
                "image_set_sha256": image_set_sha256,
                "release_sha": release_sha,
            },
        ),
        _artifact_descriptor(
            "release-bundle",
            bundle_path,
            {
                "artifact_sha256": bundle["sha256"],
                "git_commit": release_sha,
                "git_tree": expected_tree,
                "release_sha": release_sha,
            },
        ),
    ]
    receipt: dict[str, Any] = {
        "artifacts": artifacts,
        "campaign_id": campaign_id,
        "capacity_preflight": capacity_preflight,
        "image_archive": image_archive,
        "images": image_values,
        "output_directory": str(target),
        "preparation_id": preparation_id,
        "release_bundle": bundle,
        "release_sha": release_sha,
        "schema": PREPARATION_SCHEMA,
        "stage_publish": _stage_arguments(artifacts),
        "status": "prepared",
        "prepared_at": utc_iso(now or utc_now()),
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    receipt_path = target / "preparation-receipt.json"
    write_new_private_json(receipt_path, receipt)
    require_private_regular_file(
        receipt_path,
        field="detached preparation receipt",
        maximum_bytes=1024 * 1024,
    )
    return receipt


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="prepare detached Git and Docker artifacts locally")
    prepare.add_argument("--source-repo", required=True, type=Path)
    prepare.add_argument("--release-sha", required=True)
    prepare.add_argument("--workspace", required=True, type=Path)
    prepare.add_argument("--output-root", required=True, type=Path)
    prepare.add_argument("--image", action="append", default=[], metavar="REF=IMAGE_ID")
    prepare.add_argument("--git-binary", type=Path, default=Path("/usr/bin/git"))
    prepare.add_argument("--docker-binary", type=Path, default=Path("/usr/bin/docker"))
    prepare.add_argument("--preparation-id", default=None)
    prepare.add_argument(
        "--campaign-id",
        default=None,
        help="stable unique campaign ID for isolated archive tags; defaults to --preparation-id",
    )
    prepare.add_argument("--maximum-artifact-bytes", type=int, default=DEFAULT_MAXIMUM_ARTIFACT_BYTES)
    subparsers.add_parser("gitless-tree-contract", help="describe the separate fail-closed Git-less release path")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "gitless-tree-contract":
            result = gitless_release_tree_contract()
        else:
            result = prepare_artifacts(
                source_repo=args.source_repo,
                release_sha=args.release_sha,
                workspace=args.workspace,
                output_root=args.output_root,
                image_specifications=parse_image_specifications(args.image),
                git_binary=args.git_binary,
                docker_binary=args.docker_binary,
                preparation_id=args.preparation_id,
                campaign_id=args.campaign_id,
                maximum_artifact_bytes=args.maximum_artifact_bytes,
            )
    except ArtifactPreparationError as exc:
        print(json.dumps({"error": str(exc), "error_class": type(exc).__name__, "status": "blocked"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
