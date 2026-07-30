#!/usr/bin/env python3
"""Bind a prepared legacy application release to separate WA-IR control code.

The live legacy directory is deliberately not a source input.  The application
bundle and Docker archive must first be prepared by
``prepare_webapp_ir_artifact_bundle.py`` from an exact Git release and existing
images.  This helper adds one separately verified control Git bundle, writes a
provenance artifact, and later installs the two Git bundles from an already
verified private/versioned artifact-stage candidate.

It has no S3, Docker, SSH, service, routing, or ``current`` operation.  It
never creates an archive of a deployed worktree.  The only mutable destination
paths are fresh immutable release roots and a create-only root-only receipt.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Mapping, Sequence


PREPARATION_SCHEMA = "gold-trade-wa-ir-artifact-preparation-v1"
IMAGE_MANIFEST_SCHEMA = "gold-trade-wa-ir-image-manifest-v1"
PROVENANCE_SCHEMA = "gold-trade-wa-ir-release-provenance-v1"
INSTALL_RECEIPT_SCHEMA = "gold-trade-wa-ir-release-provenance-install-receipt-v1"
STAGE_RECEIPT_SCHEMA = "gold-trade-wa-ir-artifact-stage-receipt-v1"

LEGACY_APPLICATION_RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
APPLICATION_BUNDLE_ARTIFACT = "release-bundle"
IMAGE_BUNDLE_ARTIFACT = "image-bundle"
IMAGE_MANIFEST_ARTIFACT = "image-manifest"
CONTROL_BUNDLE_ARTIFACT = "control-release-bundle"
PROVENANCE_ARTIFACT = "release-provenance"
APPLICATION_RELEASE_PARENT = Path("/srv/trading-bot-three-site/releases")
CONTROL_RELEASE_PARENT = Path("/srv/trading-bot-three-site/control-releases")
STAGE_SOURCE_SITE = "webapp_fi"
STAGE_DESTINATION_SITE = "webapp_ir"
GIT_BINARY = Path("/usr/bin/git")
# Keep the same upper bounds accepted by the local preparation primitive.  A
# later stage consumer can impose a lower deployment-specific capacity limit,
# but this verifier must not reinterpret a valid prepared receipt solely due to
# a smaller parser limit.
MAX_JSON_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 100 * 1024 * 1024 * 1024

SHA_RE = re.compile(r"^[a-f0-9]{40,64}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
# Match the preparation primitive's accepted immutable Docker repo-digest
# syntax.  Registry ports and mixed-case registry names are valid inputs and
# are only carried as verified manifest data here; they are never executed.
IMAGE_DIGEST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,511}@sha256:[a-f0-9]{64}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
ARTIFACT_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
SITE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ReleaseProvenanceError(RuntimeError):
    """Raised when an immutable application/control binding is invalid."""


@dataclass(frozen=True)
class ReleaseIdentity:
    release_sha: str
    tree_sha: str
    bundle_artifact: str
    bundle_sha256: str
    release_root: Path


@dataclass(frozen=True)
class RuntimeImageContract:
    app_image_id: str
    app_repo_digest: str
    image_bundle_sha256: str
    image_manifest_sha256: str
    image_set_sha256: str
    image_ids_sha256: str
    image_count: int


@dataclass(frozen=True)
class Provenance:
    application: ReleaseIdentity
    control: ReleaseIdentity
    runtime_images: RuntimeImageContract


@dataclass(frozen=True)
class Artifact:
    name: str
    path: Path
    sha256: str
    bytes: int
    bindings: dict[str, str]


@dataclass(frozen=True)
class Preparation:
    receipt_path: Path
    receipt_sha256: str
    release_sha: str
    release_tree: str
    output_directory: Path
    artifacts: dict[str, Artifact]
    images: tuple[dict[str, Any], ...]
    image_archive: dict[str, Any]


@dataclass(frozen=True)
class StageReceipt:
    path: Path
    receipt_sha256: str
    source_site: str
    destination_site: str
    release_sha: str
    bundle_id: str
    candidate_directory: Path
    artifacts: dict[str, Artifact]


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
    except OSError as exc:
        raise ReleaseProvenanceError(f"cannot read {path}") from exc
    return digest.hexdigest(), total


def _require_root() -> None:
    if os.geteuid() != 0:
        raise ReleaseProvenanceError("this command must run as root")


def _safe_path(value: object, *, field: str) -> Path:
    if not isinstance(value, str) or not PATH_RE.fullmatch(value):
        raise ReleaseProvenanceError(f"{field} must be a safe absolute path")
    return Path(value)


def _require_sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ReleaseProvenanceError(f"{field} must be a full lowercase Git SHA")
    return value


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ReleaseProvenanceError(f"{field} must be a lowercase SHA-256")
    return value


def _require_directory(path: Path, *, field: str, private: bool) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseProvenanceError(f"{field} does not exist") from exc
    disallowed = 0o077 if private else 0o022
    if (
        not path.is_absolute()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & disallowed
        or path.resolve(strict=True) != path
    ):
        qualifier = "root-only" if private else "root-owned and not group/world writable"
        raise ReleaseProvenanceError(f"{field} must be an absolute {qualifier} directory")
    return path


def _require_file(path: Path, *, field: str, private: bool, maximum: int) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseProvenanceError(f"{field} does not exist") from exc
    disallowed = 0o077 if private else 0o022
    if (
        not path.is_absolute()
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & disallowed
        or metadata.st_nlink != 1
        or not 1 <= metadata.st_size <= maximum
    ):
        qualifier = "root-only" if private else "root-owned and not group/world writable"
        raise ReleaseProvenanceError(f"{field} must be an absolute {qualifier} regular file")
    return path


def _secure_read(path: Path, *, field: str, private: bool, maximum: int) -> bytes:
    _require_file(path, field=field, private=private, maximum=maximum)
    before = path.lstat()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseProvenanceError(f"cannot securely open {field}") from exc
    try:
        after = os.fstat(descriptor)
        disallowed = 0o077 if private else 0o022
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_uid != 0
            or after.st_mode & disallowed
            or after.st_nlink != 1
            or not 1 <= after.st_size <= maximum
            or after.st_ino != before.st_ino
            or after.st_dev != before.st_dev
        ):
            raise ReleaseProvenanceError(f"{field} changed while being opened")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise ReleaseProvenanceError(f"{field} exceeds its size limit")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _strict_json(raw: bytes, *, field: str) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise ReleaseProvenanceError(f"{field} contains duplicate JSON key {key}")
            output[key] = value
        return output

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseProvenanceError(f"{field} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseProvenanceError(f"{field} must be a JSON object")
    return value


def _read_private_json(path: Path, *, field: str, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
    return _strict_json(_secure_read(path, field=field, private=True, maximum=maximum), field=field)


def _fields(value: Mapping[str, Any], *, expected: set[str], field: str) -> None:
    if set(value) == expected:
        return
    pieces: list[str] = []
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing:
        pieces.append("missing " + ", ".join(missing))
    if extra:
        pieces.append("unexpected " + ", ".join(extra))
    raise ReleaseProvenanceError(f"{field} fields are invalid: " + "; ".join(pieces))


def _bindings(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError(f"{field} must be an object")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not ARTIFACT_RE.fullmatch(key):
            raise ReleaseProvenanceError(f"{field} contains an unsafe key")
        if not isinstance(item, str) or not item or len(item) > 512:
            raise ReleaseProvenanceError(f"{field}.{key} is invalid")
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in item):
            raise ReleaseProvenanceError(f"{field}.{key} is invalid")
        result[key] = item
    return dict(sorted(result.items()))


def _artifact(
    value: object,
    *,
    expected_name: str,
    expected_path: Path,
    field: str,
) -> Artifact:
    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError(f"{field} must be an object")
    _fields(value, expected={"name", "path", "sha256", "bytes", "bindings"}, field=field)
    if value.get("name") != expected_name:
        raise ReleaseProvenanceError(f"{field} name is not pinned")
    path = _safe_path(value.get("path"), field=f"{field} path")
    if path != expected_path:
        raise ReleaseProvenanceError(f"{field} path is not pinned to its detached preparation")
    _require_file(path, field=field, private=True, maximum=MAX_ARTIFACT_BYTES)
    digest = _require_sha256(value.get("sha256"), field=f"{field} sha256")
    bytes_value = value.get("bytes")
    if isinstance(bytes_value, bool) or not isinstance(bytes_value, int) or not 1 <= bytes_value <= MAX_ARTIFACT_BYTES:
        raise ReleaseProvenanceError(f"{field} bytes is invalid")
    actual_digest, actual_bytes = sha256_file(path)
    if actual_digest != digest or actual_bytes != bytes_value:
        raise ReleaseProvenanceError(f"{field} no longer matches its detached descriptor")
    return Artifact(
        name=expected_name,
        path=path,
        sha256=digest,
        bytes=bytes_value,
        bindings=_bindings(value.get("bindings"), field=f"{field} bindings"),
    )


def _image_values(value: object, *, field: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or not value:
        raise ReleaseProvenanceError(f"{field} must be a non-empty list")
    images: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ReleaseProvenanceError(f"{field} contains an invalid image")
        _fields(
            item,
            expected={"source_ref", "image_id", "repo_digests", "repo_tags", "size_bytes"},
            field=f"{field} image",
        )
        image_id = item.get("image_id")
        source_ref = item.get("source_ref")
        repo_digests = item.get("repo_digests")
        repo_tags = item.get("repo_tags")
        size_bytes = item.get("size_bytes")
        if (
            not isinstance(image_id, str)
            or not IMAGE_ID_RE.fullmatch(image_id)
            or image_id in seen_ids
            or not isinstance(source_ref, str)
            or not source_ref
            or not isinstance(repo_digests, list)
            or not all(isinstance(entry, str) and IMAGE_DIGEST_RE.fullmatch(entry) for entry in repo_digests)
            or len(set(repo_digests)) != len(repo_digests)
            or not isinstance(repo_tags, list)
            or not all(isinstance(entry, str) and entry for entry in repo_tags)
            or len(set(repo_tags)) != len(repo_tags)
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
        ):
            raise ReleaseProvenanceError(f"{field} image is invalid")
        seen_ids.add(image_id)
        images.append(dict(item))
    if images != sorted(images, key=lambda item: item["source_ref"]):
        raise ReleaseProvenanceError(f"{field} is not deterministically sorted")
    return tuple(images)


def _image_manifest(
    path: Path,
    *,
    release_sha: str,
    images: tuple[dict[str, Any], ...],
    archive: Mapping[str, Any],
) -> dict[str, Any]:
    value = _read_private_json(path, field="prepared image manifest")
    _fields(
        value,
        expected={"schema", "status", "release_sha", "archive", "image_set_sha256", "images"},
        field="prepared image manifest",
    )
    if value.get("schema") != IMAGE_MANIFEST_SCHEMA or value.get("status") != "prepared":
        raise ReleaseProvenanceError("prepared image manifest schema or status is unsupported")
    if value.get("release_sha") != release_sha or value.get("images") != list(images) or value.get("archive") != archive:
        raise ReleaseProvenanceError("prepared image manifest does not bind the preparation receipt")
    image_set_sha = _require_sha256(value.get("image_set_sha256"), field="prepared image manifest image_set_sha256")
    if image_set_sha != sha256_bytes(canonical_json_bytes(list(images))):
        raise ReleaseProvenanceError("prepared image manifest image set hash is invalid")
    return value


def _preparation_receipt(path: Path) -> Preparation:
    value = _read_private_json(path, field="application preparation receipt")
    _fields(
        value,
        expected={
            "artifacts",
            "capacity_preflight",
            "image_archive",
            "images",
            "output_directory",
            "preparation_id",
            "release_bundle",
            "release_sha",
            "schema",
            "stage_publish",
            "status",
            "prepared_at",
            "receipt_sha256",
        },
        field="application preparation receipt",
    )
    if value.get("schema") != PREPARATION_SCHEMA or value.get("status") != "prepared":
        raise ReleaseProvenanceError("application preparation receipt schema or status is unsupported")
    receipt_sha = _require_sha256(value.get("receipt_sha256"), field="application preparation receipt receipt_sha256")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if sha256_bytes(canonical_json_bytes(unsigned)) != receipt_sha:
        raise ReleaseProvenanceError("application preparation receipt hash is invalid")
    release_sha = _require_sha(value.get("release_sha"), field="application preparation release_sha")
    if release_sha != LEGACY_APPLICATION_RELEASE_SHA:
        raise ReleaseProvenanceError("application preparation is not the fixed legacy 2c08 release")
    prepared_at = value.get("prepared_at")
    if not isinstance(prepared_at, str) or not prepared_at.endswith("Z"):
        raise ReleaseProvenanceError("application preparation timestamp is invalid")
    try:
        if dt.datetime.fromisoformat(prepared_at.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise ReleaseProvenanceError("application preparation timestamp is invalid") from exc
    preparation_id = value.get("preparation_id")
    if not isinstance(preparation_id, str) or not BUNDLE_ID_RE.fullmatch(preparation_id):
        raise ReleaseProvenanceError("application preparation ID is invalid")
    output = _safe_path(value.get("output_directory"), field="application preparation output_directory")
    _require_directory(output, field="application preparation output_directory", private=True)
    if path != output / "preparation-receipt.json":
        raise ReleaseProvenanceError("application preparation receipt must reside in its detached output directory")
    artifacts_value = value.get("artifacts")
    if not isinstance(artifacts_value, list) or len(artifacts_value) != 3:
        raise ReleaseProvenanceError("application preparation artifacts are invalid")
    expected_paths = {
        APPLICATION_BUNDLE_ARTIFACT: output / "release.bundle",
        IMAGE_BUNDLE_ARTIFACT: output / "images.tar",
        IMAGE_MANIFEST_ARTIFACT: output / "image-manifest.json",
    }
    artifacts: dict[str, Artifact] = {}
    for raw in artifacts_value:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("name"), str):
            raise ReleaseProvenanceError("application preparation artifact is invalid")
        name = raw["name"]
        if name not in expected_paths or name in artifacts:
            raise ReleaseProvenanceError("application preparation artifact names are invalid")
        artifacts[name] = _artifact(
            raw,
            expected_name=name,
            expected_path=expected_paths[name],
            field=f"application preparation {name}",
        )
    try:
        actual_files = {entry.name for entry in output.iterdir()}
    except OSError as exc:
        raise ReleaseProvenanceError("cannot inspect application preparation output") from exc
    if actual_files != {"release.bundle", "images.tar", "image-manifest.json", "preparation-receipt.json"}:
        raise ReleaseProvenanceError("application preparation output contains unexpected files")
    release_bundle = value.get("release_bundle")
    if not isinstance(release_bundle, Mapping):
        raise ReleaseProvenanceError("application preparation release bundle is invalid")
    _fields(
        release_bundle,
        expected={"bytes", "git_commit", "git_tree", "sha256"},
        field="application preparation release bundle",
    )
    release_tree = _require_sha(release_bundle.get("git_tree"), field="application preparation release tree")
    if (
        release_bundle.get("git_commit") != release_sha
        or release_bundle.get("sha256") != artifacts[APPLICATION_BUNDLE_ARTIFACT].sha256
        or release_bundle.get("bytes") != artifacts[APPLICATION_BUNDLE_ARTIFACT].bytes
    ):
        raise ReleaseProvenanceError("application preparation release bundle does not match its artifact")
    expected_release_bindings = {
        "artifact_sha256": artifacts[APPLICATION_BUNDLE_ARTIFACT].sha256,
        "git_commit": release_sha,
        "git_tree": release_tree,
        "release_sha": release_sha,
    }
    if artifacts[APPLICATION_BUNDLE_ARTIFACT].bindings != expected_release_bindings:
        raise ReleaseProvenanceError("application release bundle bindings are invalid")
    images = _image_values(value.get("images"), field="application preparation images")
    archive = value.get("image_archive")
    if not isinstance(archive, Mapping):
        raise ReleaseProvenanceError("application preparation image archive is invalid")
    _fields(archive, expected={"bytes", "sha256", "image_ids", "repo_tags"}, field="application preparation image archive")
    archive_sha = _require_sha256(archive.get("sha256"), field="application preparation image archive sha256")
    if archive_sha != artifacts[IMAGE_BUNDLE_ARTIFACT].sha256 or archive.get("bytes") != artifacts[IMAGE_BUNDLE_ARTIFACT].bytes:
        raise ReleaseProvenanceError("application preparation image archive does not match its artifact")
    image_ids = archive.get("image_ids")
    if not isinstance(image_ids, list) or not all(isinstance(item, str) and IMAGE_ID_RE.fullmatch(item) for item in image_ids):
        raise ReleaseProvenanceError("application preparation image IDs are invalid")
    if image_ids != sorted(image_ids) or len(set(image_ids)) != len(image_ids) or set(image_ids) != {item["image_id"] for item in images}:
        raise ReleaseProvenanceError("application preparation image IDs do not match inspected images")
    image_set_sha = sha256_bytes(canonical_json_bytes(list(images)))
    image_ids_sha = sha256_bytes(canonical_json_bytes([item["image_id"] for item in images]))
    manifest = _image_manifest(
        artifacts[IMAGE_MANIFEST_ARTIFACT].path,
        release_sha=release_sha,
        images=images,
        archive=archive,
    )
    if manifest["image_set_sha256"] != image_set_sha:
        raise ReleaseProvenanceError("application preparation image manifest set hash is invalid")
    expected_manifest_bindings = {
        "artifact_sha256": artifacts[IMAGE_MANIFEST_ARTIFACT].sha256,
        "image_set_sha256": image_set_sha,
        "release_sha": release_sha,
    }
    if artifacts[IMAGE_MANIFEST_ARTIFACT].bindings != expected_manifest_bindings:
        raise ReleaseProvenanceError("application image manifest bindings are invalid")
    expected_image_bindings = {
        "artifact_sha256": archive_sha,
        "image_count": str(len(images)),
        "image_ids_sha256": image_ids_sha,
        "image_manifest_sha256": artifacts[IMAGE_MANIFEST_ARTIFACT].sha256,
        "image_set_sha256": image_set_sha,
        "release_sha": release_sha,
    }
    if artifacts[IMAGE_BUNDLE_ARTIFACT].bindings != expected_image_bindings:
        raise ReleaseProvenanceError("application image bundle bindings are invalid")
    capacity = value.get("capacity_preflight")
    if not isinstance(capacity, Mapping):
        raise ReleaseProvenanceError("application preparation capacity preflight is invalid")
    _fields(
        capacity,
        expected={
            "image_logical_bytes",
            "output_required_bytes",
            "output_free_bytes",
            "workspace_required_bytes",
            "workspace_free_bytes",
        },
        field="application preparation capacity preflight",
    )
    for key, item in capacity.items():
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise ReleaseProvenanceError(f"application preparation capacity preflight {key} is invalid")
    stage_publish = value.get("stage_publish")
    if not isinstance(stage_publish, Mapping):
        raise ReleaseProvenanceError("application preparation stage publish arguments are invalid")
    _fields(stage_publish, expected={"artifact", "artifact_binding"}, field="application preparation stage publish")
    expected_stage_publish = {
        "artifact": [
            name + "=" + str(artifacts[name].path)
            for name in sorted(artifacts)
        ],
        "artifact_binding": [
            name + "=" + key + "=" + item
            for name in sorted(artifacts)
            for key, item in sorted(artifacts[name].bindings.items())
        ],
    }
    if stage_publish != expected_stage_publish:
        raise ReleaseProvenanceError("application preparation stage publish arguments do not bind its artifacts")
    return Preparation(
        receipt_path=path,
        receipt_sha256=receipt_sha,
        release_sha=release_sha,
        release_tree=release_tree,
        output_directory=output,
        artifacts=artifacts,
        images=images,
        image_archive=dict(archive),
    )


def _release_root(value: object, *, parent: Path, sha: str, field: str, must_absent: bool) -> Path:
    path = _safe_path(value, field=field)
    _require_directory(parent, field=f"{field} parent", private=False)
    if path.parent != parent or path.name != sha:
        raise ReleaseProvenanceError(f"{field} must be the exact immutable root for its Git SHA")
    if must_absent:
        if path.exists() or path.is_symlink():
            raise ReleaseProvenanceError(f"{field} must not already exist before installation")
    else:
        _require_directory(path, field=field, private=False)
    return path


def _identity(value: object, *, role: str, artifact: str, parent: Path, must_absent: bool) -> ReleaseIdentity:
    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError(f"{role} provenance must be an object")
    _fields(
        value,
        expected={"release_sha", "tree_sha", "bundle_artifact", "bundle_sha256", "release_root"},
        field=f"{role} provenance",
    )
    sha = _require_sha(value.get("release_sha"), field=f"{role} release_sha")
    tree = _require_sha(value.get("tree_sha"), field=f"{role} tree_sha")
    if value.get("bundle_artifact") != artifact:
        raise ReleaseProvenanceError(f"{role} bundle artifact is not pinned")
    return ReleaseIdentity(
        release_sha=sha,
        tree_sha=tree,
        bundle_artifact=artifact,
        bundle_sha256=_require_sha256(value.get("bundle_sha256"), field=f"{role} bundle_sha256"),
        release_root=_release_root(
            value.get("release_root"), parent=parent, sha=sha, field=f"{role} release_root", must_absent=must_absent
        ),
    )


def _runtime_contract(value: object) -> RuntimeImageContract:
    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError("runtime_images provenance must be an object")
    _fields(
        value,
        expected={
            "app_image_id",
            "app_repo_digest",
            "image_bundle_sha256",
            "image_manifest_sha256",
            "image_set_sha256",
            "image_ids_sha256",
            "image_count",
        },
        field="runtime_images provenance",
    )
    app_image_id = value.get("app_image_id")
    app_repo_digest = value.get("app_repo_digest")
    if not isinstance(app_image_id, str) or not IMAGE_ID_RE.fullmatch(app_image_id):
        raise ReleaseProvenanceError("runtime app_image_id is invalid")
    if not isinstance(app_repo_digest, str) or not IMAGE_DIGEST_RE.fullmatch(app_repo_digest):
        raise ReleaseProvenanceError("runtime app_repo_digest is invalid")
    image_count = value.get("image_count")
    if isinstance(image_count, bool) or not isinstance(image_count, int) or image_count < 1:
        raise ReleaseProvenanceError("runtime image_count is invalid")
    return RuntimeImageContract(
        app_image_id=app_image_id,
        app_repo_digest=app_repo_digest,
        image_bundle_sha256=_require_sha256(value.get("image_bundle_sha256"), field="runtime image_bundle_sha256"),
        image_manifest_sha256=_require_sha256(
            value.get("image_manifest_sha256"), field="runtime image_manifest_sha256"
        ),
        image_set_sha256=_require_sha256(value.get("image_set_sha256"), field="runtime image_set_sha256"),
        image_ids_sha256=_require_sha256(value.get("image_ids_sha256"), field="runtime image_ids_sha256"),
        image_count=image_count,
    )


def load_provenance(path: Path, *, must_absent: bool) -> Provenance:
    value = _read_private_json(path, field="release provenance")
    _fields(value, expected={"schema", "application", "control", "runtime_images"}, field="release provenance")
    if value.get("schema") != PROVENANCE_SCHEMA:
        raise ReleaseProvenanceError("release provenance schema is unsupported")
    application = _identity(
        value.get("application"),
        role="application",
        artifact=APPLICATION_BUNDLE_ARTIFACT,
        parent=APPLICATION_RELEASE_PARENT,
        must_absent=must_absent,
    )
    control = _identity(
        value.get("control"),
        role="control",
        artifact=CONTROL_BUNDLE_ARTIFACT,
        parent=CONTROL_RELEASE_PARENT,
        must_absent=must_absent,
    )
    if application.release_sha != LEGACY_APPLICATION_RELEASE_SHA or application.release_sha == control.release_sha:
        raise ReleaseProvenanceError("application/control release identities are invalid")
    return Provenance(application=application, control=control, runtime_images=_runtime_contract(value.get("runtime_images")))


def _stage_artifact(value: object, *, candidate: Path) -> Artifact:
    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError("stage artifact must be an object")
    _fields(
        value,
        expected={
            "name",
            "sha256",
            "bytes",
            "object_key",
            "version_id",
            "ciphertext_sha256",
            "ciphertext_bytes",
            "bindings",
        },
        field="stage artifact",
    )
    name = value.get("name")
    if not isinstance(name, str) or not ARTIFACT_RE.fullmatch(name):
        raise ReleaseProvenanceError("stage artifact name is invalid")
    path = candidate / name
    _require_file(path, field=f"staged {name}", private=True, maximum=MAX_ARTIFACT_BYTES)
    digest = _require_sha256(value.get("sha256"), field=f"staged {name} sha256")
    bytes_value = value.get("bytes")
    if isinstance(bytes_value, bool) or not isinstance(bytes_value, int) or not 1 <= bytes_value <= MAX_ARTIFACT_BYTES:
        raise ReleaseProvenanceError(f"staged {name} bytes is invalid")
    actual_digest, actual_bytes = sha256_file(path)
    if actual_digest != digest or actual_bytes != bytes_value:
        raise ReleaseProvenanceError(f"staged {name} no longer matches its signed descriptor")
    _require_sha256(value.get("ciphertext_sha256"), field=f"staged {name} ciphertext_sha256")
    if isinstance(value.get("ciphertext_bytes"), bool) or not isinstance(value.get("ciphertext_bytes"), int):
        raise ReleaseProvenanceError(f"staged {name} ciphertext_bytes is invalid")
    if not isinstance(value.get("object_key"), str) or not value["object_key"] or not isinstance(value.get("version_id"), str) or not value["version_id"]:
        raise ReleaseProvenanceError(f"staged {name} object identity is invalid")
    return Artifact(name=name, path=path, sha256=digest, bytes=bytes_value, bindings=_bindings(value.get("bindings"), field=f"staged {name} bindings"))


def load_stage_receipt(path: Path) -> StageReceipt:
    value = _read_private_json(path, field="artifact stage receipt")
    _fields(
        value,
        expected={
            "schema", "status", "source_site", "destination_site", "release_sha", "bundle_id", "published_at", "staged_at",
            "candidate_directory", "manifest", "artifacts", "receipt_sha256",
        },
        field="artifact stage receipt",
    )
    if value.get("schema") != STAGE_RECEIPT_SCHEMA or value.get("status") != "staged":
        raise ReleaseProvenanceError("artifact stage receipt schema or status is unsupported")
    receipt_sha = _require_sha256(value.get("receipt_sha256"), field="artifact stage receipt receipt_sha256")
    if sha256_bytes(canonical_json_bytes({key: item for key, item in value.items() if key != "receipt_sha256"})) != receipt_sha:
        raise ReleaseProvenanceError("artifact stage receipt hash is invalid")
    source_site = value.get("source_site")
    destination_site = value.get("destination_site")
    if not isinstance(source_site, str) or not SITE_RE.fullmatch(source_site) or not isinstance(destination_site, str) or not SITE_RE.fullmatch(destination_site):
        raise ReleaseProvenanceError("artifact stage receipt site is invalid")
    release_sha = _require_sha(value.get("release_sha"), field="artifact stage receipt release_sha")
    bundle_id = value.get("bundle_id")
    if not isinstance(bundle_id, str) or not BUNDLE_ID_RE.fullmatch(bundle_id):
        raise ReleaseProvenanceError("artifact stage receipt bundle_id is invalid")
    candidate = _safe_path(value.get("candidate_directory"), field="artifact stage candidate_directory")
    _require_directory(candidate, field="detached artifact stage candidate", private=True)
    if path != candidate / "stage-receipt.json":
        raise ReleaseProvenanceError("artifact stage receipt must be the detached candidate receipt")
    raw_artifacts = value.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise ReleaseProvenanceError("artifact stage receipt artifacts are invalid")
    artifacts: dict[str, Artifact] = {}
    for raw in raw_artifacts:
        artifact = _stage_artifact(raw, candidate=candidate)
        if artifact.name in artifacts:
            raise ReleaseProvenanceError("artifact stage receipt has duplicate artifact names")
        artifacts[artifact.name] = artifact
    if {entry.name for entry in candidate.iterdir()} != {"stage-receipt.json", *artifacts}:
        raise ReleaseProvenanceError("artifact stage candidate contains unexpected files")
    return StageReceipt(
        path=path,
        receipt_sha256=receipt_sha,
        source_site=source_site,
        destination_site=destination_site,
        release_sha=release_sha,
        bundle_id=bundle_id,
        candidate_directory=candidate,
        artifacts=artifacts,
    )


def _exact_bindings(artifact: Artifact, expected: Mapping[str, str]) -> None:
    if artifact.bindings != dict(sorted(expected.items())):
        raise ReleaseProvenanceError(f"staged {artifact.name} bindings do not match release provenance")


def _validate_staged_image_manifest(path: Path, provenance: Provenance) -> None:
    value = _read_private_json(path, field="staged image manifest")
    _fields(value, expected={"schema", "status", "release_sha", "archive", "image_set_sha256", "images"}, field="staged image manifest")
    if value.get("schema") != IMAGE_MANIFEST_SCHEMA or value.get("status") != "prepared" or value.get("release_sha") != provenance.application.release_sha:
        raise ReleaseProvenanceError("staged image manifest schema or release is invalid")
    images = _image_values(value.get("images"), field="staged image manifest images")
    image_set = sha256_bytes(canonical_json_bytes(list(images)))
    image_ids = sha256_bytes(canonical_json_bytes([item["image_id"] for item in images]))
    archive = value.get("archive")
    if not isinstance(archive, Mapping):
        raise ReleaseProvenanceError("staged image manifest archive is invalid")
    if (
        value.get("image_set_sha256") != provenance.runtime_images.image_set_sha256
        or image_set != provenance.runtime_images.image_set_sha256
        or image_ids != provenance.runtime_images.image_ids_sha256
        or len(images) != provenance.runtime_images.image_count
        or archive.get("sha256") != provenance.runtime_images.image_bundle_sha256
    ):
        raise ReleaseProvenanceError("staged image manifest does not match release provenance")
    matched = [item for item in images if item["image_id"] == provenance.runtime_images.app_image_id]
    if len(matched) != 1 or provenance.runtime_images.app_repo_digest not in matched[0]["repo_digests"]:
        raise ReleaseProvenanceError("staged image manifest does not contain the pinned application image")


def verify_staged_provenance(stage_receipt_path: Path) -> tuple[StageReceipt, Provenance]:
    receipt = load_stage_receipt(stage_receipt_path)
    if receipt.source_site != STAGE_SOURCE_SITE or receipt.destination_site != STAGE_DESTINATION_SITE:
        raise ReleaseProvenanceError("artifact stage is not the fixed webapp_fi to webapp_ir transfer")
    expected_names = {
        APPLICATION_BUNDLE_ARTIFACT,
        IMAGE_BUNDLE_ARTIFACT,
        IMAGE_MANIFEST_ARTIFACT,
        CONTROL_BUNDLE_ARTIFACT,
        PROVENANCE_ARTIFACT,
    }
    if set(receipt.artifacts) != expected_names:
        raise ReleaseProvenanceError("staged candidate must contain exactly the prepared application and control artifacts")
    provenance = load_provenance(receipt.artifacts[PROVENANCE_ARTIFACT].path, must_absent=True)
    if receipt.release_sha != provenance.application.release_sha:
        raise ReleaseProvenanceError("artifact stage namespace is not pinned to the application release")
    app_bundle = receipt.artifacts[APPLICATION_BUNDLE_ARTIFACT]
    if app_bundle.sha256 != provenance.application.bundle_sha256:
        raise ReleaseProvenanceError("staged application bundle does not match release provenance")
    _exact_bindings(
        app_bundle,
        {
            "artifact_sha256": provenance.application.bundle_sha256,
            "git_commit": provenance.application.release_sha,
            "git_tree": provenance.application.tree_sha,
            "release_sha": provenance.application.release_sha,
        },
    )
    control_bundle = receipt.artifacts[CONTROL_BUNDLE_ARTIFACT]
    if control_bundle.sha256 != provenance.control.bundle_sha256:
        raise ReleaseProvenanceError("staged control bundle does not match release provenance")
    _exact_bindings(
        control_bundle,
        {
            "artifact_sha256": provenance.control.bundle_sha256,
            "control_release_sha": provenance.control.release_sha,
            "git_commit": provenance.control.release_sha,
            "git_tree": provenance.control.tree_sha,
            "release_sha": provenance.application.release_sha,
        },
    )
    image_bundle = receipt.artifacts[IMAGE_BUNDLE_ARTIFACT]
    manifest = receipt.artifacts[IMAGE_MANIFEST_ARTIFACT]
    _exact_bindings(
        image_bundle,
        {
            "artifact_sha256": image_bundle.sha256,
            "image_count": str(provenance.runtime_images.image_count),
            "image_ids_sha256": provenance.runtime_images.image_ids_sha256,
            "image_manifest_sha256": provenance.runtime_images.image_manifest_sha256,
            "image_set_sha256": provenance.runtime_images.image_set_sha256,
            "release_sha": provenance.application.release_sha,
        },
    )
    _exact_bindings(
        manifest,
        {
            "artifact_sha256": manifest.sha256,
            "image_set_sha256": provenance.runtime_images.image_set_sha256,
            "release_sha": provenance.application.release_sha,
        },
    )
    _exact_bindings(
        receipt.artifacts[PROVENANCE_ARTIFACT],
        {
            "application_bundle_sha256": provenance.application.bundle_sha256,
            "application_release_sha": provenance.application.release_sha,
            "artifact_sha256": receipt.artifacts[PROVENANCE_ARTIFACT].sha256,
            "control_bundle_sha256": provenance.control.bundle_sha256,
            "control_release_sha": provenance.control.release_sha,
            "image_manifest_sha256": provenance.runtime_images.image_manifest_sha256,
        },
    )
    if image_bundle.sha256 != provenance.runtime_images.image_bundle_sha256 or manifest.sha256 != provenance.runtime_images.image_manifest_sha256:
        raise ReleaseProvenanceError("staged image artifacts do not match release provenance")
    _validate_staged_image_manifest(manifest.path, provenance)
    return receipt, provenance


def _git_binary() -> Path:
    _require_file(GIT_BINARY, field="fixed git binary", private=False, maximum=100 * 1024 * 1024)
    if not os.access(GIT_BINARY, os.X_OK):
        raise ReleaseProvenanceError("fixed git binary is not executable")
    return GIT_BINARY


def _git_env() -> dict[str, str]:
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "file",
    }


def _git(arguments: Sequence[str], *, timeout: int = 300) -> str:
    try:
        result = subprocess.run(
            [str(_git_binary()), *[str(item) for item in arguments]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout,
            # Installed application static assets must remain readable by the
            # local unprivileged Nginx worker.  Git source contains code only;
            # secrets stay in root-only configuration outside either release.
            umask=0o022,
            env=_git_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseProvenanceError("cannot run fixed local Git command") from exc
    if result.returncode != 0:
        raise ReleaseProvenanceError("fixed local Git command rejected the requested immutable release")
    return result.stdout.strip()


def _inspect_commit(repository: Path, sha: str) -> tuple[str, str]:
    _require_directory(repository, field="control source repository", private=False)
    try:
        worktree = _git(("-C", str(repository), "rev-parse", "--is-inside-work-tree"), timeout=30)
    except ReleaseProvenanceError as exc:
        raise ReleaseProvenanceError("control source repository is not a Git worktree") from exc
    if worktree != "true":
        raise ReleaseProvenanceError("control source repository is not a Git worktree")
    git_dir = _safe_path(
        _git(("-C", str(repository), "rev-parse", "--absolute-git-dir"), timeout=30),
        field="control source repository Git directory",
    )
    _require_directory(git_dir, field="control source repository Git directory", private=False)
    sha = _require_sha(sha, field="control release SHA")
    commit = _git(("-C", str(repository), "rev-parse", f"{sha}^{{commit}}"), timeout=30)
    tree = _git(("-C", str(repository), "rev-parse", f"{sha}^{{tree}}"), timeout=30)
    if commit != sha or not SHA_RE.fullmatch(tree):
        raise ReleaseProvenanceError("control source repository does not contain the exact requested commit and tree")
    return commit, tree


def _create_git_bundle(repository: Path, sha: str, output: Path) -> None:
    if output.exists() or output.is_symlink():
        raise ReleaseProvenanceError("refusing to overwrite a control release bundle")
    with tempfile.TemporaryDirectory(prefix="wa-ir-control-bundle-", dir=str(output.parent)) as raw:
        temporary = Path(raw)
        bare = temporary / "source.git"
        _git(("init", "--bare", "--quiet", str(bare)), timeout=30)
        _git(
            (
                "-C", str(bare), "-c", "protocol.file.allow=always", "fetch", "--no-tags", str(repository),
                f"{sha}:refs/heads/control-release",
            ),
            timeout=600,
        )
        bundle = temporary / "control.bundle"
        _git(("-C", str(bare), "bundle", "create", str(bundle), "refs/heads/control-release"), timeout=600)
        bundle.chmod(0o600)
        try:
            os.link(bundle, output)
        except FileExistsError as exc:
            raise ReleaseProvenanceError("refusing to overwrite a control release bundle") from exc
        except OSError as exc:
            raise ReleaseProvenanceError("cannot create a control release bundle") from exc


def _verify_checked_out_release(identity: ReleaseIdentity, destination: Path, *, field: str) -> None:
    """Revalidate a root before first use and every receipt load."""

    _require_directory(destination, field=field, private=False)
    if (
        _git(("-C", str(destination), "rev-parse", "HEAD"), timeout=30) != identity.release_sha
        or _git(("-C", str(destination), "rev-parse", "HEAD^{tree}"), timeout=30) != identity.tree_sha
        or _git(("-C", str(destination), "status", "--porcelain=v1", "--untracked-files=all"), timeout=30)
    ):
        raise ReleaseProvenanceError(f"{field} does not match its pinned Git identity")
    for directory, names, files in os.walk(destination, followlinks=False):
        for name in [*names, *files]:
            metadata = (Path(directory) / name).lstat()
            if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
                raise ReleaseProvenanceError(f"{field} contains an unsafe path")


def _verify_checkout(bundle: Path, identity: ReleaseIdentity, destination: Path) -> None:
    _require_file(bundle, field=f"{identity.bundle_artifact} bundle", private=True, maximum=MAX_ARTIFACT_BYTES)
    if destination.exists() or destination.is_symlink():
        raise ReleaseProvenanceError("refusing to overwrite an immutable release root")
    try:
        destination.mkdir(mode=0o755)
    except FileExistsError as exc:
        # A concurrent creator must never be removed as a failed temporary
        # checkout.  The root was not created by this invocation.
        raise ReleaseProvenanceError("refusing to overwrite an immutable release root") from exc
    except OSError as exc:
        raise ReleaseProvenanceError("cannot create an immutable release root") from exc
    try:
        destination.chmod(0o755)
        _git(("-c", "protocol.file.allow=always", "clone", "--no-checkout", "--no-tags", str(bundle), str(destination)), timeout=600)
        _git(("-C", str(destination), "checkout", "--detach", "--force", identity.release_sha), timeout=300)
        _verify_checked_out_release(identity, destination, field="checked-out immutable release root")
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _create_only_json(path: Path, payload: Mapping[str, Any]) -> None:
    _require_directory(path.parent, field="receipt parent", private=True)
    if path.exists() or path.is_symlink():
        raise ReleaseProvenanceError("refusing to overwrite a release provenance receipt")
    temporary = path.parent / ("." + path.name + ".tmp-" + os.urandom(8).hex())
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(payload) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError as exc:
        raise ReleaseProvenanceError("refusing to overwrite a release provenance receipt") from exc
    except OSError as exc:
        raise ReleaseProvenanceError("cannot create a release provenance receipt") from exc
    finally:
        temporary.unlink(missing_ok=True)


def build_control_artifacts(
    *,
    application_preparation_receipt: Path,
    control_repository: Path,
    control_release_sha: str,
    output_directory: Path,
    app_image_id: str,
    app_repo_digest: str,
) -> dict[str, Any]:
    """Create only control/provenance artifacts tied to a prepared app receipt."""

    preparation = _preparation_receipt(application_preparation_receipt)
    control_sha, control_tree = _inspect_commit(control_repository, control_release_sha)
    if control_sha == preparation.release_sha:
        raise ReleaseProvenanceError("application and control commits must be distinct")
    if not IMAGE_ID_RE.fullmatch(app_image_id) or not IMAGE_DIGEST_RE.fullmatch(app_repo_digest):
        raise ReleaseProvenanceError("pinned application image identity is invalid")
    selected = [item for item in preparation.images if item["image_id"] == app_image_id]
    if len(selected) != 1 or app_repo_digest not in selected[0]["repo_digests"]:
        raise ReleaseProvenanceError("pinned application image is absent from the prepared image manifest")
    output = _safe_path(str(output_directory), field="output_directory")
    _require_directory(output.parent, field="output_directory parent", private=True)
    if output.exists() or output.is_symlink():
        raise ReleaseProvenanceError("output_directory must be a new immutable directory")
    runtime = RuntimeImageContract(
        app_image_id=app_image_id,
        app_repo_digest=app_repo_digest,
        image_bundle_sha256=preparation.artifacts[IMAGE_BUNDLE_ARTIFACT].sha256,
        image_manifest_sha256=preparation.artifacts[IMAGE_MANIFEST_ARTIFACT].sha256,
        image_set_sha256=preparation.artifacts[IMAGE_MANIFEST_ARTIFACT].bindings["image_set_sha256"],
        image_ids_sha256=preparation.artifacts[IMAGE_BUNDLE_ARTIFACT].bindings["image_ids_sha256"],
        image_count=len(preparation.images),
    )
    output.mkdir(mode=0o700)
    # Retain a failed fresh candidate without a success receipt, matching the
    # preparation primitive.  A retry must use a different path; an operator
    # can inspect the failure rather than silently losing forensic evidence.
    _create_git_bundle(control_repository, control_sha, output / CONTROL_BUNDLE_ARTIFACT)
    control_sha256, control_bytes = sha256_file(output / CONTROL_BUNDLE_ARTIFACT)
    provenance_payload: dict[str, Any] = {
        "schema": PROVENANCE_SCHEMA,
        "application": {
            "release_sha": preparation.release_sha,
            "tree_sha": preparation.release_tree,
            "bundle_artifact": APPLICATION_BUNDLE_ARTIFACT,
            "bundle_sha256": preparation.artifacts[APPLICATION_BUNDLE_ARTIFACT].sha256,
            "release_root": str(APPLICATION_RELEASE_PARENT / preparation.release_sha),
        },
        "control": {
            "release_sha": control_sha,
            "tree_sha": control_tree,
            "bundle_artifact": CONTROL_BUNDLE_ARTIFACT,
            "bundle_sha256": control_sha256,
            "release_root": str(CONTROL_RELEASE_PARENT / control_sha),
        },
        "runtime_images": {
            "app_image_id": runtime.app_image_id,
            "app_repo_digest": runtime.app_repo_digest,
            "image_bundle_sha256": runtime.image_bundle_sha256,
            "image_manifest_sha256": runtime.image_manifest_sha256,
            "image_set_sha256": runtime.image_set_sha256,
            "image_ids_sha256": runtime.image_ids_sha256,
            "image_count": runtime.image_count,
        },
    }
    _create_only_json(output / PROVENANCE_ARTIFACT, provenance_payload)
    provenance_sha256, provenance_bytes = sha256_file(output / PROVENANCE_ARTIFACT)
    artifact_specs = {
        APPLICATION_BUNDLE_ARTIFACT: preparation.artifacts[APPLICATION_BUNDLE_ARTIFACT],
        IMAGE_BUNDLE_ARTIFACT: preparation.artifacts[IMAGE_BUNDLE_ARTIFACT],
        IMAGE_MANIFEST_ARTIFACT: preparation.artifacts[IMAGE_MANIFEST_ARTIFACT],
        CONTROL_BUNDLE_ARTIFACT: Artifact(
            name=CONTROL_BUNDLE_ARTIFACT,
            path=output / CONTROL_BUNDLE_ARTIFACT,
            sha256=control_sha256,
            bytes=control_bytes,
            bindings={
                "artifact_sha256": control_sha256,
                "control_release_sha": control_sha,
                "git_commit": control_sha,
                "git_tree": control_tree,
                "release_sha": preparation.release_sha,
            },
        ),
        PROVENANCE_ARTIFACT: Artifact(
            name=PROVENANCE_ARTIFACT,
            path=output / PROVENANCE_ARTIFACT,
            sha256=provenance_sha256,
            bytes=provenance_bytes,
            bindings={
                "application_bundle_sha256": preparation.artifacts[APPLICATION_BUNDLE_ARTIFACT].sha256,
                "application_release_sha": preparation.release_sha,
                "artifact_sha256": provenance_sha256,
                "control_bundle_sha256": control_sha256,
                "control_release_sha": control_sha,
                "image_manifest_sha256": runtime.image_manifest_sha256,
            },
        ),
    }
    return {
        "schema": PROVENANCE_SCHEMA,
        "status": "prepared",
        "application_preparation_receipt": str(preparation.receipt_path),
        "output_directory": str(output),
        "application": provenance_payload["application"],
        "control": provenance_payload["control"],
        "runtime_images": provenance_payload["runtime_images"],
        "stage_publish": {
            "artifact": [name + "=" + str(artifact_specs[name].path) for name in sorted(artifact_specs)],
            "artifact_binding": [
                name + "=" + key + "=" + value
                for name in sorted(artifact_specs)
                for key, value in sorted(artifact_specs[name].bindings.items())
            ],
        },
    }


def _install_receipt(stage: StageReceipt, provenance: Provenance, *, now: dt.datetime) -> dict[str, Any]:
    timestamp = now.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema": INSTALL_RECEIPT_SCHEMA,
        "status": "installed",
        "installed_at": timestamp,
        "stage": {
            "source_site": stage.source_site,
            "destination_site": stage.destination_site,
            "release_sha": stage.release_sha,
            "bundle_id": stage.bundle_id,
            "receipt_sha256": stage.receipt_sha256,
        },
        "application": {
            "release_sha": provenance.application.release_sha,
            "tree_sha": provenance.application.tree_sha,
            "release_root": str(provenance.application.release_root),
            "bundle_sha256": stage.artifacts[APPLICATION_BUNDLE_ARTIFACT].sha256,
        },
        "control": {
            "release_sha": provenance.control.release_sha,
            "tree_sha": provenance.control.tree_sha,
            "release_root": str(provenance.control.release_root),
            "bundle_sha256": stage.artifacts[CONTROL_BUNDLE_ARTIFACT].sha256,
        },
        "runtime_images": {
            "image_bundle_sha256": provenance.runtime_images.image_bundle_sha256,
            "image_manifest_sha256": provenance.runtime_images.image_manifest_sha256,
            "image_set_sha256": provenance.runtime_images.image_set_sha256,
            "image_ids_sha256": provenance.runtime_images.image_ids_sha256,
            "image_count": provenance.runtime_images.image_count,
            "app_image_id": provenance.runtime_images.app_image_id,
            "app_repo_digest": provenance.runtime_images.app_repo_digest,
        },
    }


def install_release_roots(*, stage_receipt_path: Path, receipt_path: Path, now: dt.datetime | None = None) -> dict[str, Any]:
    stage, provenance = verify_staged_provenance(stage_receipt_path)
    receipt_path = _safe_path(str(receipt_path), field="receipt_path")
    _require_directory(receipt_path.parent, field="receipt parent", private=True)
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ReleaseProvenanceError("refusing to overwrite a release provenance receipt")
    application_installed = False
    control_installed = False
    try:
        # Creating the final root with mkdir is exclusive.  It avoids an
        # overwrite-capable rename race while the receipt remains the sole
        # marker that a root is eligible for control-plane use.
        _verify_checkout(
            stage.artifacts[APPLICATION_BUNDLE_ARTIFACT].path,
            provenance.application,
            provenance.application.release_root,
        )
        application_installed = True
        _verify_checkout(
            stage.artifacts[CONTROL_BUNDLE_ARTIFACT].path,
            provenance.control,
            provenance.control.release_root,
        )
        control_installed = True
        payload = _install_receipt(stage, provenance, now=now or dt.datetime.now(dt.timezone.utc))
        _create_only_json(receipt_path, payload)
        return payload
    except Exception:
        # Only remove roots that this invocation successfully created, and
        # only if no receipt was linked.  A linked receipt is authoritative
        # even if a subsequent directory fsync reported an error.
        if not receipt_path.exists():
            if control_installed:
                shutil.rmtree(provenance.control.release_root, ignore_errors=True)
            if application_installed:
                shutil.rmtree(provenance.application.release_root, ignore_errors=True)
        raise


def _installed_identity(value: object, *, role: str, parent: Path) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError(f"installed {role} release must be an object")
    _fields(value, expected={"release_sha", "tree_sha", "release_root", "bundle_sha256"}, field=f"installed {role} release")
    sha = _require_sha(value.get("release_sha"), field=f"installed {role} release_sha")
    tree = _require_sha(value.get("tree_sha"), field=f"installed {role} tree_sha")
    root = _release_root(value.get("release_root"), parent=parent, sha=sha, field=f"installed {role} release_root", must_absent=False)
    bundle_sha256 = _require_sha256(value.get("bundle_sha256"), field=f"installed {role} bundle_sha256")
    _verify_checked_out_release(
        ReleaseIdentity(
            release_sha=sha,
            tree_sha=tree,
            bundle_artifact=role + "-bundle",
            bundle_sha256=bundle_sha256,
            release_root=root,
        ),
        root,
        field=f"installed {role} release root",
    )
    return {
        "release_sha": sha,
        "tree_sha": tree,
        "release_root": str(root),
        "bundle_sha256": bundle_sha256,
    }


def load_installed_release_receipt(path: Path) -> dict[str, Any]:
    value = _read_private_json(path, field="installed release provenance receipt")
    _fields(value, expected={"schema", "status", "installed_at", "stage", "application", "control", "runtime_images"}, field="installed release provenance receipt")
    if value.get("schema") != INSTALL_RECEIPT_SCHEMA or value.get("status") != "installed":
        raise ReleaseProvenanceError("installed release provenance receipt schema or status is unsupported")
    installed_at = value.get("installed_at")
    if not isinstance(installed_at, str) or not installed_at.endswith("Z"):
        raise ReleaseProvenanceError("installed release provenance receipt installed_at is invalid")
    try:
        if dt.datetime.fromisoformat(installed_at.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise ReleaseProvenanceError("installed release provenance receipt installed_at is invalid") from exc
    application = _installed_identity(value.get("application"), role="application", parent=APPLICATION_RELEASE_PARENT)
    control = _installed_identity(value.get("control"), role="control", parent=CONTROL_RELEASE_PARENT)
    if application["release_sha"] != LEGACY_APPLICATION_RELEASE_SHA or application["release_sha"] == control["release_sha"]:
        raise ReleaseProvenanceError("installed application/control release identities are invalid")
    stage = value.get("stage")
    if not isinstance(stage, Mapping):
        raise ReleaseProvenanceError("installed release provenance stage is invalid")
    _fields(stage, expected={"source_site", "destination_site", "release_sha", "bundle_id", "receipt_sha256"}, field="installed release provenance stage")
    if (
        stage.get("source_site") != STAGE_SOURCE_SITE
        or stage.get("destination_site") != STAGE_DESTINATION_SITE
        or _require_sha(stage.get("release_sha"), field="installed stage release_sha") != application["release_sha"]
        or not isinstance(stage.get("bundle_id"), str) or not BUNDLE_ID_RE.fullmatch(stage["bundle_id"])
    ):
        raise ReleaseProvenanceError("installed release provenance stage is invalid")
    _require_sha256(stage.get("receipt_sha256"), field="installed stage receipt_sha256")
    images = _runtime_contract(value.get("runtime_images"))
    return {
        "application": application,
        "control": control,
        "runtime_images": {
            "app_image_id": images.app_image_id,
            "app_repo_digest": images.app_repo_digest,
            "image_bundle_sha256": images.image_bundle_sha256,
            "image_manifest_sha256": images.image_manifest_sha256,
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build-control", help="prepare only a control bundle bound to a verified app preparation receipt")
    build.add_argument("--application-preparation-receipt", type=Path, required=True)
    build.add_argument("--control-repository", type=Path, required=True)
    build.add_argument("--control-release-sha", required=True)
    build.add_argument("--output-directory", type=Path, required=True)
    build.add_argument("--app-image-id", required=True)
    build.add_argument("--app-repo-digest", required=True)
    install = commands.add_parser("install", help="install fresh application/control roots from one verified stage candidate")
    install.add_argument("--stage-receipt", type=Path, required=True)
    install.add_argument("--receipt", type=Path, required=True)
    verify = commands.add_parser("verify-installed", help="revalidate the create-only receipt and immutable Git roots")
    verify.add_argument("--receipt", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        _require_root()
        if args.command == "build-control":
            result = build_control_artifacts(
                application_preparation_receipt=args.application_preparation_receipt,
                control_repository=args.control_repository,
                control_release_sha=args.control_release_sha,
                output_directory=args.output_directory,
                app_image_id=args.app_image_id,
                app_repo_digest=args.app_repo_digest,
            )
        elif args.command == "install":
            result = install_release_roots(stage_receipt_path=args.stage_receipt, receipt_path=args.receipt)
        elif args.command == "verify-installed":
            result = load_installed_release_receipt(args.receipt)
        else:  # pragma: no cover - argparse keeps this unreachable.
            raise ReleaseProvenanceError("unsupported command")
    except ReleaseProvenanceError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
