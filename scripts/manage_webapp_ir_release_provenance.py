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
import base64
import binascii
from dataclasses import dataclass
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence


def _load_image_archive_contract() -> Any:
    """Load the pure archive-tag contract from the receipt-bound control code."""

    module_path = Path(__file__).with_name("webapp_ir_image_archive_contract.py")
    spec = importlib.util.spec_from_file_location("_wa_ir_image_archive_contract", module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError("cannot load WA-IR image archive contract")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


image_contract = _load_image_archive_contract()


def _load_source_provenance_verifier() -> Any:
    """Load the pure FI/controller proof verifier shipped in the bootstrap."""

    module_path = Path(__file__).with_name("verify_webapp_fi_source_provenance.py")
    spec = importlib.util.spec_from_file_location("_webapp_fi_source_provenance", module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError("cannot load WebApp-FI source provenance verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


source_provenance = _load_source_provenance_verifier()


def _load_artifact_preparer() -> Any:
    """Load the pure Docker archive verifier from the exact sibling source file."""

    module_path = Path(__file__).with_name("prepare_webapp_ir_artifact_bundle.py")
    spec = importlib.util.spec_from_file_location("_wa_ir_artifact_preparer", module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError("cannot load WA-IR artifact preparer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


artifact_preparer = _load_artifact_preparer()


PREPARATION_SCHEMA = "gold-trade-wa-ir-artifact-preparation-v1"
IMAGE_MANIFEST_SCHEMA = "gold-trade-wa-ir-image-manifest-v1"
PROVENANCE_SCHEMA = "gold-trade-wa-ir-release-provenance-v2"
INSTALL_RECEIPT_SCHEMA = "gold-trade-wa-ir-release-provenance-install-receipt-v2"
STAGE_RECEIPT_SCHEMA = "gold-trade-wa-ir-artifact-stage-receipt-v1"
BOOTSTRAP_RECEIPT_SCHEMA = "gold-trade-wa-ir-stage-bootstrap-receipt-v1"
BOOTSTRAP_RECEIPT_NAME = "bootstrap-receipt.json"
CONSUMER_CONFIG_SCHEMA = "gold-trade-wa-ir-artifact-stage-config-v3"
BOOTSTRAP_CONSUMER_CONFIG = "config/consumer.json"
WEBAPP_FI_SOURCE_PROVENANCE_SCHEMA = "gold-trade-wa-ir-webapp-fi-source-provenance-v1"
WEBAPP_FI_SOURCE_PROVENANCE_INPUT_SCHEMA = "gold-trade-wa-ir-webapp-fi-source-provenance-input-v1"
WEBAPP_FI_SOURCE_PROOF_NAMES = (
    "source_role_attestation",
    "image_export_receipt",
    "controller_delivery_envelope",
    "signer_enrollment_certificate",
    "static_assets_provenance",
    "controller_image_adoption_receipt",
)

LEGACY_APPLICATION_RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
APPLICATION_BUNDLE_ARTIFACT = "release-bundle"
IMAGE_BUNDLE_ARTIFACT = "image-bundle"
IMAGE_MANIFEST_ARTIFACT = "image-manifest"
CONTROL_BUNDLE_ARTIFACT = "control-release-bundle"
PROVENANCE_ARTIFACT = "release-provenance"
BOOTSTRAP_RECEIPT_FILES = frozenset(
    {
        "scripts/manage_webapp_ir_artifact_stage.py",
        "scripts/manage_webapp_ir_snapshot.py",
        "scripts/manage_webapp_ir_release_provenance.py",
        "scripts/prepare_webapp_ir_artifact_bundle.py",
        "scripts/verify_webapp_fi_source_provenance.py",
        "scripts/install_webapp_ir_static_assets.py",
        "core/standby_snapshot_capacity.py",
        "scripts/webapp_ir_image_archive_contract.py",
        BOOTSTRAP_CONSUMER_CONFIG,
    }
)
APPLICATION_RELEASE_PARENT = Path("/srv/trading-bot-three-site/releases")
CONTROL_RELEASE_PARENT = Path("/srv/trading-bot-three-site/control-releases")
CONTROL_DISPATCHER_DIRECTORY = Path("/srv/trading-bot-three-site/control-dispatcher")
TRUSTED_DISPATCHER_PATH = CONTROL_DISPATCHER_DIRECTORY / "manage_webapp_ir_release_provenance.py"
CONTROL_DISPATCHER_SOURCE = Path("scripts/manage_webapp_ir_release_provenance.py")
STAGE_SOURCE_SITE = "webapp_fi"
STAGE_DESTINATION_SITE = "webapp_ir"
GIT_BINARY = Path("/usr/bin/git")
PYTHON_BINARY = Path("/usr/bin/python3")
# systemd must start this command from the fixed dispatcher path installed by
# ``install`` from an already verified control Git root, never from an
# environment-selected control release.
CONTROL_DISPATCH_TARGETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "lease-guard": (
        "scripts/production_writer_lease_agent.py",
        ("guard",),
    ),
    "promotion-coordinator": (
        "scripts/run_webapp_ir_promotion_coordinator.py",
        ("--apply", "--json"),
    ),
}
# Keep the same upper bounds accepted by the local preparation primitive.  A
# later stage consumer can impose a lower deployment-specific capacity limit,
# but this verifier must not reinterpret a valid prepared receipt solely due to
# a smaller parser limit.
MAX_JSON_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 100 * 1024 * 1024 * 1024
MAX_BOOTSTRAP_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_BOOTSTRAP_CIPHERTEXT_BYTES = MAX_BOOTSTRAP_ARCHIVE_BYTES + 2 * 1024 * 1024
# The outer release-provenance document is itself size-bounded.  Leave enough
# room for the application/control/runtime bindings around the embedded proofs.
MAX_EMBEDDED_SOURCE_PROOF_BYTES = MAX_JSON_BYTES - 64 * 1024

SHA_RE = re.compile(r"^[a-f0-9]{40,64}$")
BOOTSTRAP_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
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
OBJECT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/=-]{0,1023}$")
CONSUMER_CONFIG_FIELDS = frozenset(
    {
        "schema",
        "endpoint",
        "region",
        "bucket",
        "prefix",
        "age_binary",
        "age_identity_file",
        "workspace",
        "source_site",
        "source_signing_public_key_base64",
        "webapp_fi_source_attestation_public_key_base64",
        "webapp_fi_controller_authorization_public_key_base64",
        "maximum_artifact_bytes",
    }
)


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
class InstalledDispatcher:
    path: Path
    sha256: str
    control_release_sha: str
    directory_device: int
    directory_inode: int


@dataclass(frozen=True)
class RuntimeImageContract:
    app_image_id: str
    app_repo_digest: str | None
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
    webapp_fi_source_provenance: dict[str, Any]


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
    campaign_id: str
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


@dataclass(frozen=True)
class BootstrapReceipt:
    """URL-free proof emitted by the first WA-IR bootstrap receiver."""

    path: Path
    control_commit: str
    control_tree: str
    consumer_config_sha256: str
    webapp_fi_source_attestation_public_key: bytes
    webapp_fi_controller_authorization_public_key: bytes


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

    def reject_constant(value: str) -> None:
        raise ValueError(f"unsupported JSON constant {value}")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReleaseProvenanceError(f"{field} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseProvenanceError(f"{field} must be a JSON object")
    return value


def _read_private_json(path: Path, *, field: str, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
    return _strict_json(_secure_read(path, field=field, private=True, maximum=maximum), field=field)


def _read_canonical_private_json(path: Path, *, field: str, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
    """Read a root-only receipt whose self-hash has one unambiguous encoding."""

    raw = _secure_read(path, field=field, private=True, maximum=maximum)
    value = _strict_json(raw, field=field)
    if raw != canonical_json_bytes(value) + b"\n":
        raise ReleaseProvenanceError(f"{field} must use canonical JSON")
    return value


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


def _decode_exact_public_key(value: object, *, field: str) -> bytes:
    """Decode one canonical public Ed25519 key without retaining its text form."""

    if not isinstance(value, str) or not value or len(value) > 128:
        raise ReleaseProvenanceError(f"{field} is invalid")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise ReleaseProvenanceError(f"{field} is invalid") from exc
    if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != value:
        raise ReleaseProvenanceError(f"{field} is invalid")
    return decoded


def _bootstrap_member_path(candidate: Path, name: str) -> Path:
    """Return one fixed bootstrap member only through root-private ancestors."""

    pure = PurePosixPath(name)
    if (
        not name
        or pure.as_posix() != name
        or pure.is_absolute()
        or "\\" in name
        or any(part in ("", ".", "..") for part in pure.parts)
    ):
        raise ReleaseProvenanceError("bootstrap receipt contains an unsafe member name")
    parent = candidate
    for part in pure.parts[:-1]:
        parent = parent / part
        _require_directory(parent, field=f"bootstrap member parent for {name}", private=True)
    path = parent / pure.name
    _require_file(path, field=f"bootstrap member {name}", private=True, maximum=MAX_JSON_BYTES)
    return path


def _load_hash_bound_bootstrap_consumer_config(
    *,
    candidate_directory: Path,
    files: Mapping[str, Any],
    expected_sha256: str,
) -> tuple[bytes, bytes]:
    """Read the received v3 config only after rechecking every bootstrap member.

    The receive receipt records hashes that the constrained bootstrap receiver
    checked before extraction.  Rechecking those members here closes the gap
    between receive and immutable-root installation, including the portable
    FI source verifier that will consume these two public pins later.
    """

    observed: dict[str, str] = {}
    consumer_config_raw: bytes | None = None
    for name in sorted(BOOTSTRAP_RECEIPT_FILES):
        raw = _secure_read(
            _bootstrap_member_path(candidate_directory, name),
            field=f"bootstrap member {name}",
            private=True,
            maximum=MAX_JSON_BYTES,
        )
        observed[name] = sha256_bytes(raw)
        if name == BOOTSTRAP_CONSUMER_CONFIG:
            consumer_config_raw = raw
    if observed != {name: files[name] for name in BOOTSTRAP_RECEIPT_FILES}:
        raise ReleaseProvenanceError("bootstrap receive receipt file hashes no longer match the received candidate")
    if consumer_config_raw is None:  # pragma: no cover - fixed set invariant.
        raise ReleaseProvenanceError("bootstrap receive receipt lacks the consumer config")
    if observed[BOOTSTRAP_CONSUMER_CONFIG] != expected_sha256:
        raise ReleaseProvenanceError("bootstrap received consumer config does not match its hash-bound receipt")
    config = _strict_json(consumer_config_raw, field="bootstrap received consumer config")
    if set(config) != CONSUMER_CONFIG_FIELDS or config.get("schema") != CONSUMER_CONFIG_SCHEMA:
        raise ReleaseProvenanceError("bootstrap received consumer config is not the exact v3 schema")
    if config.get("source_site") != STAGE_SOURCE_SITE:
        raise ReleaseProvenanceError("bootstrap received consumer config is not pinned to webapp_fi")
    # The normal stage consumer verifies its own transport settings.  This
    # control-plane verifier needs only the two separately enrolled public
    # provenance keys and validates their exact wire representation here.
    _decode_exact_public_key(
        config.get("source_signing_public_key_base64"),
        field="bootstrap received consumer source signing public key",
    )
    return (
        _decode_exact_public_key(
            config.get("webapp_fi_source_attestation_public_key_base64"),
            field="bootstrap received consumer WebApp-FI source attestation public key",
        ),
        _decode_exact_public_key(
            config.get("webapp_fi_controller_authorization_public_key_base64"),
            field="bootstrap received consumer WebApp-FI controller authorization public key",
        ),
    )


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
            expected={"archive_tag", "source_ref", "image_id", "repo_digests", "repo_tags", "size_bytes"},
            field=f"{field} image",
        )
        archive_tag = item.get("archive_tag")
        image_id = item.get("image_id")
        source_ref = item.get("source_ref")
        repo_digests = item.get("repo_digests")
        repo_tags = item.get("repo_tags")
        size_bytes = item.get("size_bytes")
        if (
            not isinstance(image_id, str)
            or not IMAGE_ID_RE.fullmatch(image_id)
            or image_id in seen_ids
            or not isinstance(archive_tag, str)
            or not archive_tag
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


def _validate_isolated_archive_tags(
    images: Sequence[Mapping[str, Any]],
    *,
    campaign_id: object,
    release_sha: object,
    field: str,
) -> str:
    """Require one safe, deterministic tag for every staged image identity."""

    try:
        campaign = image_contract.require_campaign_id(campaign_id, field=f"{field} campaign_id")
        release = image_contract.require_release_sha(release_sha, field=f"{field} release_sha")
        observed: set[str] = set()
        for image in images:
            tag = image.get("archive_tag")
            image_id = image.get("image_id")
            image_contract.require_canonical_archive_tag(
                tag,
                campaign_id=campaign,
                release_sha=release,
                image_id=image_id,
                field=f"{field} image archive_tag",
            )
            if tag in observed:
                raise image_contract.ImageArchiveContractError("archive tags are duplicated")
            observed.add(tag)
    except image_contract.ImageArchiveContractError as exc:
        raise ReleaseProvenanceError(f"{field} does not use isolated image archive tags") from exc
    return campaign


def _image_manifest(
    path: Path,
    *,
    campaign_id: str,
    release_sha: str,
    images: tuple[dict[str, Any], ...],
    archive: Mapping[str, Any],
) -> dict[str, Any]:
    value = _read_private_json(path, field="prepared image manifest")
    _fields(
        value,
        expected={"schema", "status", "campaign_id", "release_sha", "archive", "image_set_sha256", "images"},
        field="prepared image manifest",
    )
    if value.get("schema") != IMAGE_MANIFEST_SCHEMA or value.get("status") != "prepared":
        raise ReleaseProvenanceError("prepared image manifest schema or status is unsupported")
    if (
        value.get("campaign_id") != campaign_id
        or value.get("release_sha") != release_sha
        or value.get("images") != list(images)
        or value.get("archive") != archive
    ):
        raise ReleaseProvenanceError("prepared image manifest does not bind the preparation receipt")
    _validate_isolated_archive_tags(images, campaign_id=campaign_id, release_sha=release_sha, field="prepared image manifest")
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
            "campaign_id",
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
    campaign_id = value.get("campaign_id")
    try:
        campaign_id = image_contract.require_campaign_id(campaign_id)
    except image_contract.ImageArchiveContractError as exc:
        raise ReleaseProvenanceError("application preparation campaign_id is invalid") from exc
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
    _validate_isolated_archive_tags(
        images,
        campaign_id=campaign_id,
        release_sha=release_sha,
        field="application preparation images",
    )
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
    archive_tags = archive.get("repo_tags")
    expected_archive_tags = sorted(item["archive_tag"] for item in images)
    if archive_tags != expected_archive_tags:
        raise ReleaseProvenanceError("application preparation image archive retains shared or noncanonical tags")
    image_set_sha = sha256_bytes(canonical_json_bytes(list(images)))
    image_ids_sha = sha256_bytes(canonical_json_bytes([item["image_id"] for item in images]))
    manifest = _image_manifest(
        artifacts[IMAGE_MANIFEST_ARTIFACT].path,
        campaign_id=campaign_id,
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
        campaign_id=campaign_id,
        release_sha=release_sha,
        release_tree=release_tree,
        output_directory=output,
        artifacts=artifacts,
        images=images,
        image_archive=dict(archive),
    )


def _release_root(value: object, *, parent: Path, sha: str, field: str, must_absent: bool | None) -> Path:
    path = _safe_path(value, field=field)
    if path.parent != parent or path.name != sha:
        raise ReleaseProvenanceError(f"{field} must be the exact immutable root for its Git SHA")
    if must_absent is None:
        return path
    _require_directory(parent, field=f"{field} parent", private=False)
    if must_absent:
        if path.exists() or path.is_symlink():
            raise ReleaseProvenanceError(f"{field} must not already exist before installation")
    else:
        _require_directory(path, field=field, private=False)
    return path


def _identity(value: object, *, role: str, artifact: str, parent: Path, must_absent: bool | None) -> ReleaseIdentity:
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
    required = {
        "app_image_id",
        "image_bundle_sha256",
        "image_manifest_sha256",
        "image_set_sha256",
        "image_ids_sha256",
        "image_count",
    }
    fields = set(value)
    if fields not in (required, required | {"app_repo_digest"}):
        raise ReleaseProvenanceError("runtime_images provenance fields are unsupported")
    app_image_id = value.get("app_image_id")
    if not isinstance(app_image_id, str) or not IMAGE_ID_RE.fullmatch(app_image_id):
        raise ReleaseProvenanceError("runtime app_image_id is invalid")
    if "app_repo_digest" in value:
        app_repo_digest = value["app_repo_digest"]
        if not isinstance(app_repo_digest, str) or not IMAGE_DIGEST_RE.fullmatch(app_repo_digest):
            raise ReleaseProvenanceError("runtime app_repo_digest is invalid")
    else:
        app_repo_digest = None
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


def _source_proof_mappings(value: object, *, field: str) -> dict[str, dict[str, Any]]:
    """Normalize six canonical URL-free proofs embedded in release provenance."""

    if not isinstance(value, Mapping) or set(value) != set(WEBAPP_FI_SOURCE_PROOF_NAMES):
        raise ReleaseProvenanceError(f"{field} must contain exactly the six required proofs")
    proofs: dict[str, dict[str, Any]] = {}
    total = 0
    for name in WEBAPP_FI_SOURCE_PROOF_NAMES:
        proof = value.get(name)
        if not isinstance(proof, Mapping):
            raise ReleaseProvenanceError(f"{field}.{name} must be a JSON object")
        normalized = dict(proof)
        try:
            payload = source_provenance.canonical_json_bytes(normalized) + b"\n"
            source_provenance._parse(payload, field=f"{field}.{name}")
        except Exception:
            raise ReleaseProvenanceError(f"{field}.{name} is not canonical URL-free proof JSON") from None
        total += len(payload)
        if total > MAX_EMBEDDED_SOURCE_PROOF_BYTES:
            raise ReleaseProvenanceError(f"{field} exceeds the embedded proof size limit")
        proofs[name] = normalized
    return proofs


def _webapp_fi_source_provenance(
    value: object,
    *,
    expected_application_release_sha: str,
    expected_app_image_id: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError("WebApp-FI source provenance must be an object")
    _fields(
        value,
        expected={
            "schema",
            "campaign_id",
            "application",
            "canonical_release_tree_sha256",
            "app_image_reference",
            "proofs",
        },
        field="WebApp-FI source provenance",
    )
    if value.get("schema") != WEBAPP_FI_SOURCE_PROVENANCE_SCHEMA:
        raise ReleaseProvenanceError("WebApp-FI source provenance schema is unsupported")
    try:
        campaign_id = source_provenance._campaign(value.get("campaign_id"), field="WebApp-FI source provenance campaign")
        application = source_provenance._application(
            value.get("application"), field="WebApp-FI source provenance application"
        )
        canonical_tree_sha256 = source_provenance._sha(
            value.get("canonical_release_tree_sha256"),
            field="WebApp-FI source provenance canonical tree SHA-256",
        )
    except Exception:
        raise ReleaseProvenanceError("WebApp-FI source provenance identity is invalid") from None
    app_image_reference = value.get("app_image_reference")
    if not isinstance(app_image_reference, str) or not source_provenance.IMAGE_REFERENCE_RE.fullmatch(app_image_reference):
        raise ReleaseProvenanceError("WebApp-FI source provenance application image reference is invalid")
    if application["release_sha"] != expected_application_release_sha:
        raise ReleaseProvenanceError("WebApp-FI source provenance application release is not pinned")
    if not IMAGE_ID_RE.fullmatch(expected_app_image_id):  # pragma: no cover - caller already validates the runtime contract.
        raise ReleaseProvenanceError("WebApp-FI source provenance application image is invalid")
    return {
        "schema": WEBAPP_FI_SOURCE_PROVENANCE_SCHEMA,
        "campaign_id": campaign_id,
        "application": application,
        "canonical_release_tree_sha256": canonical_tree_sha256,
        "app_image_reference": app_image_reference,
        "proofs": _source_proof_mappings(value.get("proofs"), field="WebApp-FI source provenance proofs"),
    }


def _load_webapp_fi_source_provenance_input(
    path: Path,
    *,
    expected_campaign_id: str,
) -> dict[str, dict[str, Any]]:
    value = _read_canonical_private_json(path, field="WebApp-FI source provenance input")
    _fields(
        value,
        expected={"schema", "campaign_id", "proofs"},
        field="WebApp-FI source provenance input",
    )
    if value.get("schema") != WEBAPP_FI_SOURCE_PROVENANCE_INPUT_SCHEMA:
        raise ReleaseProvenanceError("WebApp-FI source provenance input schema is unsupported")
    try:
        campaign_id = source_provenance._campaign(
            value.get("campaign_id"), field="WebApp-FI source provenance input campaign"
        )
    except Exception:
        raise ReleaseProvenanceError("WebApp-FI source provenance input campaign is invalid") from None
    if campaign_id != expected_campaign_id:
        raise ReleaseProvenanceError("WebApp-FI source provenance input campaign does not match preparation")
    return _source_proof_mappings(value.get("proofs"), field="WebApp-FI source provenance input proofs")


def load_provenance(path: Path, *, must_absent: bool | None) -> Provenance:
    value = _read_private_json(path, field="release provenance")
    _fields(
        value,
        expected={"schema", "application", "control", "runtime_images", "webapp_fi_source_provenance"},
        field="release provenance",
    )
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
    runtime_images = _runtime_contract(value.get("runtime_images"))
    return Provenance(
        application=application,
        control=control,
        runtime_images=runtime_images,
        webapp_fi_source_provenance=_webapp_fi_source_provenance(
            value.get("webapp_fi_source_provenance"),
            expected_application_release_sha=application.release_sha,
            expected_app_image_id=runtime_images.app_image_id,
        ),
    )


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


def load_bootstrap_receive_receipt(path: Path) -> BootstrapReceipt:
    """Validate the URL-free receipt created while installing the stage consumer.

    The bootstrap receiver is the only component allowed to consume the first
    encrypted package.  Installation of normal application/control roots must
    retain its exact reviewed control identity, rather than treating a later
    signed stage as authority to select unrelated control tooling.
    """

    value = _read_canonical_private_json(path, field="bootstrap receive receipt")
    _fields(
        value,
        expected={
            "schema",
            "status",
            "received_at",
            "source_site",
            "destination_site",
            "control_commit",
            "control_tree",
            "bootstrap_id",
            "candidate_directory",
            "files",
            "bootstrap",
            "receipt_sha256",
        },
        field="bootstrap receive receipt",
    )
    if value.get("schema") != BOOTSTRAP_RECEIPT_SCHEMA or value.get("status") != "received":
        raise ReleaseProvenanceError("bootstrap receive receipt schema or status is unsupported")
    receipt_sha = _require_sha256(value.get("receipt_sha256"), field="bootstrap receive receipt receipt_sha256")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if sha256_bytes(canonical_json_bytes(unsigned)) != receipt_sha:
        raise ReleaseProvenanceError("bootstrap receive receipt hash is invalid")
    received_at = value.get("received_at")
    if not isinstance(received_at, str) or not received_at.endswith("Z"):
        raise ReleaseProvenanceError("bootstrap receive receipt received_at is invalid")
    try:
        if dt.datetime.fromisoformat(received_at.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise ReleaseProvenanceError("bootstrap receive receipt received_at is invalid") from exc
    if value.get("source_site") != STAGE_SOURCE_SITE or value.get("destination_site") != STAGE_DESTINATION_SITE:
        raise ReleaseProvenanceError("bootstrap receive receipt is not the fixed webapp_fi to webapp_ir transfer")
    control_commit = value.get("control_commit")
    control_tree = value.get("control_tree")
    if not isinstance(control_commit, str) or not BOOTSTRAP_SHA_RE.fullmatch(control_commit):
        raise ReleaseProvenanceError("bootstrap receive receipt control_commit is invalid")
    if not isinstance(control_tree, str) or not BOOTSTRAP_SHA_RE.fullmatch(control_tree):
        raise ReleaseProvenanceError("bootstrap receive receipt control_tree is invalid")
    bootstrap_id = value.get("bootstrap_id")
    if not isinstance(bootstrap_id, str) or not BUNDLE_ID_RE.fullmatch(bootstrap_id):
        raise ReleaseProvenanceError("bootstrap receive receipt bootstrap_id is invalid")
    candidate_directory = _safe_path(
        value.get("candidate_directory"),
        field="bootstrap receive receipt candidate_directory",
    )
    _require_directory(candidate_directory, field="bootstrap receive receipt candidate_directory", private=True)
    if candidate_directory.name != f"received-{control_commit}-{bootstrap_id}":
        raise ReleaseProvenanceError("bootstrap receive receipt candidate_directory is not receipt-bound")
    if path != candidate_directory / BOOTSTRAP_RECEIPT_NAME:
        raise ReleaseProvenanceError("bootstrap receive receipt must remain at its candidate path")
    files = value.get("files")
    if not isinstance(files, Mapping) or set(files) != BOOTSTRAP_RECEIPT_FILES:
        raise ReleaseProvenanceError("bootstrap receive receipt files are invalid")
    for name in sorted(BOOTSTRAP_RECEIPT_FILES):
        _require_sha256(files.get(name), field=f"bootstrap receive receipt files.{name}")
    bootstrap = value.get("bootstrap")
    if not isinstance(bootstrap, Mapping):
        raise ReleaseProvenanceError("bootstrap receive receipt bootstrap is invalid")
    _fields(
        bootstrap,
        expected={
            "object_key",
            "version_id",
            "ciphertext_sha256",
            "ciphertext_bytes",
            "plaintext_sha256",
            "plaintext_bytes",
            "package_manifest_sha256",
            "consumer_config_sha256",
            "preparation_receipt_sha256",
        },
        field="bootstrap receive receipt bootstrap",
    )
    object_key = bootstrap.get("object_key")
    if not isinstance(object_key, str) or not OBJECT_KEY_RE.fullmatch(object_key):
        raise ReleaseProvenanceError("bootstrap receive receipt bootstrap object_key is invalid")
    version_id = bootstrap.get("version_id")
    if (
        not isinstance(version_id, str)
        or not version_id
        or len(version_id) > 1024
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in version_id)
    ):
        raise ReleaseProvenanceError("bootstrap receive receipt bootstrap version_id is invalid")
    for field in (
        "ciphertext_sha256",
        "plaintext_sha256",
        "package_manifest_sha256",
        "consumer_config_sha256",
        "preparation_receipt_sha256",
    ):
        _require_sha256(bootstrap.get(field), field=f"bootstrap receive receipt bootstrap {field}")
    if files["config/consumer.json"] != bootstrap["consumer_config_sha256"]:
        raise ReleaseProvenanceError("bootstrap receive receipt consumer config hash is inconsistent")
    plaintext_bytes = bootstrap.get("plaintext_bytes")
    ciphertext_bytes = bootstrap.get("ciphertext_bytes")
    if isinstance(plaintext_bytes, bool) or not isinstance(plaintext_bytes, int) or not 1 <= plaintext_bytes <= MAX_BOOTSTRAP_ARCHIVE_BYTES:
        raise ReleaseProvenanceError("bootstrap receive receipt bootstrap plaintext_bytes is invalid")
    if isinstance(ciphertext_bytes, bool) or not isinstance(ciphertext_bytes, int) or not 1 <= ciphertext_bytes <= MAX_BOOTSTRAP_CIPHERTEXT_BYTES:
        raise ReleaseProvenanceError("bootstrap receive receipt bootstrap ciphertext_bytes is invalid")
    (
        webapp_fi_source_attestation_public_key,
        webapp_fi_controller_authorization_public_key,
    ) = _load_hash_bound_bootstrap_consumer_config(
        candidate_directory=candidate_directory,
        files=files,
        expected_sha256=bootstrap["consumer_config_sha256"],
    )
    return BootstrapReceipt(
        path=path,
        control_commit=control_commit,
        control_tree=control_tree,
        consumer_config_sha256=bootstrap["consumer_config_sha256"],
        webapp_fi_source_attestation_public_key=webapp_fi_source_attestation_public_key,
        webapp_fi_controller_authorization_public_key=webapp_fi_controller_authorization_public_key,
    )


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


def _validate_staged_image_manifest(path: Path, provenance: Provenance) -> tuple[str, tuple[dict[str, Any], ...]]:
    value = _read_private_json(path, field="staged image manifest")
    _fields(
        value,
        expected={"schema", "status", "campaign_id", "release_sha", "archive", "image_set_sha256", "images"},
        field="staged image manifest",
    )
    if value.get("schema") != IMAGE_MANIFEST_SCHEMA or value.get("status") != "prepared" or value.get("release_sha") != provenance.application.release_sha:
        raise ReleaseProvenanceError("staged image manifest schema or release is invalid")
    images = _image_values(value.get("images"), field="staged image manifest images")
    _validate_isolated_archive_tags(
        images,
        campaign_id=value.get("campaign_id"),
        release_sha=value.get("release_sha"),
        field="staged image manifest",
    )
    image_set = sha256_bytes(canonical_json_bytes(list(images)))
    image_ids = sha256_bytes(canonical_json_bytes([item["image_id"] for item in images]))
    archive = value.get("archive")
    if not isinstance(archive, Mapping):
        raise ReleaseProvenanceError("staged image manifest archive is invalid")
    archive_tags = archive.get("repo_tags")
    if archive_tags != sorted(item["archive_tag"] for item in images):
        raise ReleaseProvenanceError("staged image manifest archive retains shared or noncanonical tags")
    if (
        value.get("image_set_sha256") != provenance.runtime_images.image_set_sha256
        or image_set != provenance.runtime_images.image_set_sha256
        or image_ids != provenance.runtime_images.image_ids_sha256
        or len(images) != provenance.runtime_images.image_count
        or archive.get("sha256") != provenance.runtime_images.image_bundle_sha256
    ):
        raise ReleaseProvenanceError("staged image manifest does not match release provenance")
    matched = [item for item in images if item["image_id"] == provenance.runtime_images.app_image_id]
    if len(matched) != 1:
        raise ReleaseProvenanceError("staged image manifest does not contain the pinned application image")
    app_repo_digest = provenance.runtime_images.app_repo_digest
    if app_repo_digest is None:
        if matched[0]["repo_digests"]:
            raise ReleaseProvenanceError("staged image manifest has an unpinned application repo digest")
    elif app_repo_digest not in matched[0]["repo_digests"]:
        raise ReleaseProvenanceError("staged image manifest does not contain the pinned application image")
    return str(value["campaign_id"]), images


def _verify_staged_image_archive(path: Path, images: Sequence[Mapping[str, Any]]) -> None:
    """Require the exact staged bytes to be a canonical isolated Docker archive."""

    try:
        expected = tuple(
            artifact_preparer.PreparedImage(
                source_ref=str(image["source_ref"]),
                image_id=str(image["image_id"]),
                repo_digests=tuple(image["repo_digests"]),
                repo_tags=tuple(image["repo_tags"]),
                size_bytes=int(image["size_bytes"]),
                archive_tag=str(image["archive_tag"]),
            )
            for image in images
        )
        artifact_preparer.verify_docker_image_archive(
            path=path,
            images=expected,
            require_isolated_tags=True,
        )
    except Exception:
        raise ReleaseProvenanceError("staged image archive does not match the isolated image manifest") from None


def _verify_artifact_set(
    *,
    artifacts: Mapping[str, Artifact],
    release_sha: str,
    must_absent: bool | None,
) -> Provenance:
    expected_names = {
        APPLICATION_BUNDLE_ARTIFACT,
        IMAGE_BUNDLE_ARTIFACT,
        IMAGE_MANIFEST_ARTIFACT,
        CONTROL_BUNDLE_ARTIFACT,
        PROVENANCE_ARTIFACT,
    }
    if set(artifacts) != expected_names:
        raise ReleaseProvenanceError("artifact candidate must contain exactly the prepared application and control artifacts")
    provenance = load_provenance(artifacts[PROVENANCE_ARTIFACT].path, must_absent=must_absent)
    if release_sha != provenance.application.release_sha:
        raise ReleaseProvenanceError("artifact stage namespace is not pinned to the application release")
    app_bundle = artifacts[APPLICATION_BUNDLE_ARTIFACT]
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
    control_bundle = artifacts[CONTROL_BUNDLE_ARTIFACT]
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
    image_bundle = artifacts[IMAGE_BUNDLE_ARTIFACT]
    manifest = artifacts[IMAGE_MANIFEST_ARTIFACT]
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
        artifacts[PROVENANCE_ARTIFACT],
        {
            "application_bundle_sha256": provenance.application.bundle_sha256,
            "application_release_sha": provenance.application.release_sha,
            "artifact_sha256": artifacts[PROVENANCE_ARTIFACT].sha256,
            "control_bundle_sha256": provenance.control.bundle_sha256,
            "control_release_sha": provenance.control.release_sha,
            "image_manifest_sha256": provenance.runtime_images.image_manifest_sha256,
        },
    )
    if image_bundle.sha256 != provenance.runtime_images.image_bundle_sha256 or manifest.sha256 != provenance.runtime_images.image_manifest_sha256:
        raise ReleaseProvenanceError("staged image artifacts do not match release provenance")
    _, images = _validate_staged_image_manifest(manifest.path, provenance)
    _verify_staged_image_archive(image_bundle.path, images)
    return provenance


def _source_proof_payloads(provenance: Provenance) -> dict[str, bytes]:
    proofs = provenance.webapp_fi_source_provenance["proofs"]
    return {
        name: source_provenance.canonical_json_bytes(proofs[name]) + b"\n"
        for name in WEBAPP_FI_SOURCE_PROOF_NAMES
    }


def _verification_anchor_from_adoption(provenance: Provenance) -> str:
    """Use the controller-signed adoption instant for offline WA-IR revalidation.

    The controller checks freshness against its real wall clock before the
    first object/SSH effect.  A later WA-IR verification must not reject the
    same immutable candidate merely because a multi-gigabyte transfer lasted
    longer than the source observation window.  The full composite verifier
    authenticates this anchor before accepting it.
    """

    adoption = provenance.webapp_fi_source_provenance["proofs"]["controller_image_adoption_receipt"]
    try:
        return source_provenance._fresh_timestamp(
            adoption.get("adopted_at"),
            field="controller image adoption timestamp",
            verification_time=adoption.get("adopted_at"),
            maximum_age_seconds=source_provenance.MAX_OBSERVATION_AGE_SECONDS,
        )
    except Exception:
        raise ReleaseProvenanceError("controller image adoption verification anchor is invalid") from None


def _verify_webapp_fi_source_composite(
    *,
    provenance: Provenance,
    artifacts: Mapping[str, Artifact],
    control_commit: str,
    control_tree: str,
    pinned_source_public_key: bytes,
    pinned_controller_public_key: bytes,
    verification_time: str,
) -> dict[str, Any]:
    try:
        control_commit = source_provenance._tooling(
            {"control_commit": control_commit, "control_tree": control_tree},
            field="bootstrap control identity",
        )["control_commit"]
        control_tree = source_provenance._tooling(
            {"control_commit": control_commit, "control_tree": control_tree},
            field="bootstrap control identity",
        )["control_tree"]
        source_provenance._timestamp(verification_time, field="source provenance verification time")
    except Exception:
        raise ReleaseProvenanceError("bootstrap control identity or source verification time is invalid") from None
    if (
        provenance.control.release_sha != control_commit
        or provenance.control.tree_sha != control_tree
        or len(pinned_source_public_key) != 32
        or len(pinned_controller_public_key) != 32
    ):
        raise ReleaseProvenanceError("release provenance does not match the pinned bootstrap control identity")
    source_claim = provenance.webapp_fi_source_provenance
    payloads = _source_proof_payloads(provenance)
    image_bundle = artifacts[IMAGE_BUNDLE_ARTIFACT]
    image_manifest = artifacts[IMAGE_MANIFEST_ARTIFACT]
    try:
        composite = source_provenance.verify_composite_webapp_fi_source_provenance(
            source_role_attestation_payload=payloads["source_role_attestation"],
            image_export_receipt_payload=payloads["image_export_receipt"],
            controller_delivery_envelope_payload=payloads["controller_delivery_envelope"],
            signer_enrollment_certificate_payload=payloads["signer_enrollment_certificate"],
            static_assets_provenance_payload=payloads["static_assets_provenance"],
            controller_image_adoption_receipt_payload=payloads["controller_image_adoption_receipt"],
            pinned_source_signing_public_key_base64=base64.b64encode(pinned_source_public_key).decode("ascii"),
            pinned_controller_public_key_base64=base64.b64encode(pinned_controller_public_key).decode("ascii"),
            expected_campaign_id=source_claim["campaign_id"],
            expected_application=source_claim["application"],
            expected_control_commit=control_commit,
            expected_control_tree=control_tree,
            expected_canonical_release_tree_sha256=source_claim["canonical_release_tree_sha256"],
            expected_app_image_id=provenance.runtime_images.app_image_id,
            expected_app_image_reference=source_claim["app_image_reference"],
            expected_image_bundle_sha256=image_bundle.sha256,
            expected_image_bundle_bytes=image_bundle.bytes,
            expected_image_manifest_sha256=image_manifest.sha256,
            expected_image_manifest_bytes=image_manifest.bytes,
            verification_time=verification_time,
        )
    except Exception:
        raise ReleaseProvenanceError("WebApp-FI/controller composite source provenance is invalid") from None
    try:
        adoption_artifacts = composite["image_adoption"]["controller_image_artifacts"]
        if (
            adoption_artifacts["image_set_sha256"] != provenance.runtime_images.image_set_sha256
            or adoption_artifacts["image_ids_sha256"] != provenance.runtime_images.image_ids_sha256
            or adoption_artifacts["app_image_id"] != provenance.runtime_images.app_image_id
        ):
            raise KeyError
        campaign_id, images = _validate_staged_image_manifest(image_manifest.path, provenance)
        matched = [item for item in images if item["image_id"] == provenance.runtime_images.app_image_id]
        if (
            campaign_id != source_claim["campaign_id"]
            or len(matched) != 1
            or matched[0]["source_ref"] != source_claim["app_image_reference"]
            or matched[0]["archive_tag"] != adoption_artifacts["app_image_archive_tag"]
        ):
            raise KeyError
    except (KeyError, TypeError):
        raise ReleaseProvenanceError("WebApp-FI/controller image adoption does not match the staged image manifest") from None
    return composite


def _publish_input_artifacts(
    artifacts: Sequence[Any],
    *,
    maximum_artifact_bytes: int,
) -> dict[str, Artifact]:
    if isinstance(maximum_artifact_bytes, bool) or not isinstance(maximum_artifact_bytes, int) or not 1 <= maximum_artifact_bytes <= MAX_ARTIFACT_BYTES:
        raise ReleaseProvenanceError("publish artifact size limit is invalid")
    result: dict[str, Artifact] = {}
    for item in artifacts:
        name = getattr(item, "name", None)
        path = getattr(item, "path", None)
        bindings = getattr(item, "bindings", None)
        if not isinstance(name, str) or not ARTIFACT_RE.fullmatch(name) or not isinstance(path, Path):
            raise ReleaseProvenanceError("publish artifact input is invalid")
        if name in result:
            raise ReleaseProvenanceError("publish artifact input has duplicate names")
        _require_file(path, field=f"publish artifact {name}", private=True, maximum=maximum_artifact_bytes)
        digest, size = sha256_file(path)
        result[name] = Artifact(
            name=name,
            path=path,
            sha256=digest,
            bytes=size,
            bindings=_bindings(bindings, field=f"publish artifact {name} bindings"),
        )
    return result


def verify_publishable_stage_inputs(
    *,
    artifacts: Sequence[Any],
    bootstrap_control_commit: str,
    bootstrap_control_tree: str,
    pinned_source_public_key: bytes,
    pinned_controller_public_key: bytes,
    maximum_artifact_bytes: int,
    verification_time: str,
) -> dict[str, dict[str, int | str]]:
    """Verify all five final inputs before any WA-IR/S3 side effect.

    The caller uses the returned snapshots to require those exact bytes again
    immediately before each age encryption/upload operation.
    """

    prepared = _publish_input_artifacts(artifacts, maximum_artifact_bytes=maximum_artifact_bytes)
    provenance = _verify_artifact_set(
        artifacts=prepared,
        release_sha=LEGACY_APPLICATION_RELEASE_SHA,
        must_absent=None,
    )
    _verify_webapp_fi_source_composite(
        provenance=provenance,
        artifacts=prepared,
        control_commit=bootstrap_control_commit,
        control_tree=bootstrap_control_tree,
        pinned_source_public_key=pinned_source_public_key,
        pinned_controller_public_key=pinned_controller_public_key,
        verification_time=verification_time,
    )
    return {
        name: {"sha256": artifact.sha256, "bytes": artifact.bytes}
        for name, artifact in sorted(prepared.items())
    }


def verify_staged_provenance(
    stage_receipt_path: Path,
    *,
    bootstrap: BootstrapReceipt,
) -> tuple[StageReceipt, Provenance]:
    receipt = load_stage_receipt(stage_receipt_path)
    if receipt.source_site != STAGE_SOURCE_SITE or receipt.destination_site != STAGE_DESTINATION_SITE:
        raise ReleaseProvenanceError("artifact stage is not the fixed webapp_fi to webapp_ir transfer")
    provenance = _verify_artifact_set(
        artifacts=receipt.artifacts,
        release_sha=receipt.release_sha,
        must_absent=True,
    )
    _verify_webapp_fi_source_composite(
        provenance=provenance,
        artifacts=receipt.artifacts,
        control_commit=bootstrap.control_commit,
        control_tree=bootstrap.control_tree,
        pinned_source_public_key=bootstrap.webapp_fi_source_attestation_public_key,
        pinned_controller_public_key=bootstrap.webapp_fi_controller_authorization_public_key,
        verification_time=_verification_anchor_from_adoption(provenance),
    )
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
    expected_alembic_revision: str,
    canonical_release_tree_sha256: str,
    webapp_fi_source_provenance_input: Path,
    app_repo_digest: str | None = None,
) -> dict[str, Any]:
    """Create only control/provenance artifacts tied to a prepared app receipt."""

    preparation = _preparation_receipt(application_preparation_receipt)
    control_sha, control_tree = _inspect_commit(control_repository, control_release_sha)
    if control_sha == preparation.release_sha:
        raise ReleaseProvenanceError("application and control commits must be distinct")
    if not IMAGE_ID_RE.fullmatch(app_image_id):
        raise ReleaseProvenanceError("pinned application image identity is invalid")
    if app_repo_digest is not None and (
        not isinstance(app_repo_digest, str) or not IMAGE_DIGEST_RE.fullmatch(app_repo_digest)
    ):
        raise ReleaseProvenanceError("pinned application image identity is invalid")
    selected = [item for item in preparation.images if item["image_id"] == app_image_id]
    if len(selected) != 1:
        raise ReleaseProvenanceError("pinned application image is absent from the prepared image manifest")
    if app_repo_digest is None:
        if selected[0]["repo_digests"]:
            raise ReleaseProvenanceError("pinned application image has a repo digest that must be bound")
    elif app_repo_digest not in selected[0]["repo_digests"]:
        raise ReleaseProvenanceError("pinned application image is absent from the prepared image manifest")
    app_image_reference = selected[0]["source_ref"]
    if not isinstance(app_image_reference, str) or not source_provenance.IMAGE_REFERENCE_RE.fullmatch(app_image_reference):
        raise ReleaseProvenanceError("prepared application image source reference is invalid")
    try:
        source_application = source_provenance._application(
            {
                "release_sha": preparation.release_sha,
                "expected_alembic_revision": expected_alembic_revision,
            },
            field="expected application",
        )
        canonical_tree_sha256 = source_provenance._sha(
            canonical_release_tree_sha256,
            field="expected canonical release tree SHA-256",
        )
    except Exception:
        raise ReleaseProvenanceError("WebApp-FI source provenance identity input is invalid") from None
    source_proofs = _load_webapp_fi_source_provenance_input(
        webapp_fi_source_provenance_input,
        expected_campaign_id=preparation.campaign_id,
    )
    embedded_source_provenance = _webapp_fi_source_provenance(
        {
            "schema": WEBAPP_FI_SOURCE_PROVENANCE_SCHEMA,
            "campaign_id": preparation.campaign_id,
            "application": source_application,
            "canonical_release_tree_sha256": canonical_tree_sha256,
            "app_image_reference": app_image_reference,
            "proofs": source_proofs,
        },
        expected_application_release_sha=preparation.release_sha,
        expected_app_image_id=app_image_id,
    )
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
    runtime_images: dict[str, Any] = {
        "app_image_id": runtime.app_image_id,
        "image_bundle_sha256": runtime.image_bundle_sha256,
        "image_manifest_sha256": runtime.image_manifest_sha256,
        "image_set_sha256": runtime.image_set_sha256,
        "image_ids_sha256": runtime.image_ids_sha256,
        "image_count": runtime.image_count,
    }
    if runtime.app_repo_digest is not None:
        runtime_images["app_repo_digest"] = runtime.app_repo_digest
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
        "runtime_images": runtime_images,
        "webapp_fi_source_provenance": embedded_source_provenance,
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
        "webapp_fi_source_provenance": {
            "campaign_id": embedded_source_provenance["campaign_id"],
            "proof_sha256": {
                name: sha256_bytes(source_provenance.canonical_json_bytes(source_proofs[name]) + b"\n")
                for name in WEBAPP_FI_SOURCE_PROOF_NAMES
            },
        },
        "stage_publish": {
            "artifact": [name + "=" + str(artifact_specs[name].path) for name in sorted(artifact_specs)],
            "artifact_binding": [
                name + "=" + key + "=" + value
                for name in sorted(artifact_specs)
                for key, value in sorted(artifact_specs[name].bindings.items())
            ],
        },
    }


def _remove_new_dispatcher(dispatcher: InstalledDispatcher) -> None:
    """Remove only the dispatcher directory created by this failed install."""

    directory = dispatcher.path.parent
    try:
        metadata = directory.lstat()
    except OSError:
        return
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_dev != dispatcher.directory_device
        or metadata.st_ino != dispatcher.directory_inode
    ):
        return
    try:
        entries = {entry.name: entry for entry in directory.iterdir()}
    except OSError:
        return
    if entries:
        if set(entries) != {dispatcher.path.name}:
            return
        try:
            actual_sha, _ = sha256_file(dispatcher.path)
        except ReleaseProvenanceError:
            return
        if actual_sha != dispatcher.sha256:
            return
    try:
        if entries:
            dispatcher.path.unlink()
        directory.rmdir()
        parent_descriptor = os.open(
            directory.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    except OSError:
        return


def _install_fixed_dispatcher(control: ReleaseIdentity) -> InstalledDispatcher:
    """Atomically create the one fixed dispatcher from verified control code."""

    if TRUSTED_DISPATCHER_PATH.parent != CONTROL_DISPATCHER_DIRECTORY:
        raise ReleaseProvenanceError("fixed control dispatcher path is invalid")
    _require_directory(
        CONTROL_DISPATCHER_DIRECTORY.parent,
        field="fixed control dispatcher parent",
        private=False,
    )
    if CONTROL_DISPATCHER_DIRECTORY.exists() or CONTROL_DISPATCHER_DIRECTORY.is_symlink():
        raise ReleaseProvenanceError("fixed control dispatcher directory must not already exist")
    source = control.release_root / CONTROL_DISPATCHER_SOURCE
    source_payload = _secure_read(
        source,
        field="verified control dispatcher source",
        private=False,
        maximum=4 * 1024 * 1024,
    )
    source_sha256 = sha256_bytes(source_payload)
    temporary: Path | None = None
    installed: InstalledDispatcher | None = None
    try:
        CONTROL_DISPATCHER_DIRECTORY.mkdir(mode=0o755)
        parent_descriptor = os.open(
            CONTROL_DISPATCHER_DIRECTORY.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
        directory = _require_directory(
            CONTROL_DISPATCHER_DIRECTORY,
            field="fixed control dispatcher directory",
            private=False,
        )
        directory_metadata = directory.stat()
        temporary = directory / ("." + TRUSTED_DISPATCHER_PATH.name + ".tmp-" + os.urandom(8).hex())
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o644,
        )
        try:
            offset = 0
            while offset < len(source_payload):
                written = os.write(descriptor, source_payload[offset:])
                if written <= 0:
                    raise OSError("short dispatcher write")
                offset += written
            os.fchmod(descriptor, 0o644)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        # ``link`` is create-only: unlike rename it can never replace a
        # pre-existing dispatcher path.
        os.link(temporary, TRUSTED_DISPATCHER_PATH, follow_symlinks=False)
        temporary.unlink()
        temporary = None
        directory_descriptor = os.open(
            directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        actual_sha256, _ = sha256_file(TRUSTED_DISPATCHER_PATH)
        if actual_sha256 != source_sha256:
            raise ReleaseProvenanceError("fixed control dispatcher hash does not match verified control source")
        installed = InstalledDispatcher(
            path=TRUSTED_DISPATCHER_PATH,
            sha256=source_sha256,
            control_release_sha=control.release_sha,
            directory_device=directory_metadata.st_dev,
            directory_inode=directory_metadata.st_ino,
        )
        return installed
    except (OSError, ReleaseProvenanceError) as exc:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        if installed is None:
            try:
                directory_metadata = CONTROL_DISPATCHER_DIRECTORY.lstat()
            except OSError:
                directory_metadata = None
            if directory_metadata is not None and stat.S_ISDIR(directory_metadata.st_mode):
                provisional = InstalledDispatcher(
                    path=TRUSTED_DISPATCHER_PATH,
                    sha256=source_sha256,
                    control_release_sha=control.release_sha,
                    directory_device=directory_metadata.st_dev,
                    directory_inode=directory_metadata.st_ino,
                )
                _remove_new_dispatcher(provisional)
        if isinstance(exc, ReleaseProvenanceError):
            raise
        raise ReleaseProvenanceError("cannot create fixed control dispatcher") from exc


def _install_receipt(
    stage: StageReceipt,
    provenance: Provenance,
    dispatcher: InstalledDispatcher,
    *,
    bootstrap: BootstrapReceipt,
    now: dt.datetime,
) -> dict[str, Any]:
    timestamp = now.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    runtime_images: dict[str, Any] = {
        "image_bundle_sha256": provenance.runtime_images.image_bundle_sha256,
        "image_manifest_sha256": provenance.runtime_images.image_manifest_sha256,
        "image_set_sha256": provenance.runtime_images.image_set_sha256,
        "image_ids_sha256": provenance.runtime_images.image_ids_sha256,
        "image_count": provenance.runtime_images.image_count,
        "app_image_id": provenance.runtime_images.app_image_id,
    }
    if provenance.runtime_images.app_repo_digest is not None:
        runtime_images["app_repo_digest"] = provenance.runtime_images.app_repo_digest
    receipt: dict[str, Any] = {
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
        "dispatcher": {
            "path": str(dispatcher.path),
            "sha256": dispatcher.sha256,
            "control_release_sha": dispatcher.control_release_sha,
        },
        "runtime_images": runtime_images,
        # These are public Ed25519 pins only.  They were decoded from the
        # hash-bound received consumer config and are carried forward for the
        # later, separately implemented composite FI-source proof verifier.
        # No source proof is claimed or verified by this installation step.
        "bootstrap_provenance": {
            "consumer_config_sha256": bootstrap.consumer_config_sha256,
            "webapp_fi_source_attestation_public_key_base64": base64.b64encode(
                bootstrap.webapp_fi_source_attestation_public_key
            ).decode("ascii"),
            "webapp_fi_controller_authorization_public_key_base64": base64.b64encode(
                bootstrap.webapp_fi_controller_authorization_public_key
            ).decode("ascii"),
        },
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    return receipt


def install_release_roots(
    *,
    stage_receipt_path: Path,
    bootstrap_receipt_path: Path,
    receipt_path: Path,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    bootstrap = load_bootstrap_receive_receipt(bootstrap_receipt_path)
    stage, provenance = verify_staged_provenance(stage_receipt_path, bootstrap=bootstrap)
    if (
        bootstrap.control_commit != provenance.control.release_sha
        or bootstrap.control_tree != provenance.control.tree_sha
    ):
        raise ReleaseProvenanceError(
            "staged control release does not match the validated bootstrap control identity"
        )
    receipt_path = _safe_path(str(receipt_path), field="receipt_path")
    _require_directory(receipt_path.parent, field="receipt parent", private=True)
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ReleaseProvenanceError("refusing to overwrite a release provenance receipt")
    application_installed = False
    control_installed = False
    dispatcher: InstalledDispatcher | None = None
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
        dispatcher = _install_fixed_dispatcher(provenance.control)
        payload = _install_receipt(
            stage,
            provenance,
            dispatcher,
            bootstrap=bootstrap,
            now=now or dt.datetime.now(dt.timezone.utc),
        )
        _create_only_json(receipt_path, payload)
        return payload
    except Exception:
        # Only remove roots that this invocation successfully created, and
        # only if no receipt was linked.  A linked receipt is authoritative
        # even if a subsequent directory fsync reported an error.
        if not receipt_path.exists():
            if dispatcher is not None:
                _remove_new_dispatcher(dispatcher)
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


def _installed_dispatcher(value: object, *, control: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError("installed fixed control dispatcher must be an object")
    _fields(
        value,
        expected={"path", "sha256", "control_release_sha"},
        field="installed fixed control dispatcher",
    )
    path = _safe_path(value.get("path"), field="installed fixed control dispatcher path")
    if path != TRUSTED_DISPATCHER_PATH:
        raise ReleaseProvenanceError("installed fixed control dispatcher path is not pinned")
    sha256 = _require_sha256(value.get("sha256"), field="installed fixed control dispatcher sha256")
    control_release_sha = _require_sha(
        value.get("control_release_sha"),
        field="installed fixed control dispatcher control_release_sha",
    )
    if control_release_sha != control["release_sha"]:
        raise ReleaseProvenanceError("installed fixed control dispatcher is not bound to the control release")
    _require_file(
        path,
        field="installed fixed control dispatcher",
        private=False,
        maximum=4 * 1024 * 1024,
    )
    actual_sha256, _ = sha256_file(path)
    if actual_sha256 != sha256:
        raise ReleaseProvenanceError("installed fixed control dispatcher hash does not match its receipt")
    return {
        "path": str(path),
        "sha256": sha256,
        "control_release_sha": control_release_sha,
    }


def _installed_bootstrap_provenance(value: object) -> dict[str, str]:
    """Validate the public, config-hash-bound pins persisted by install v2."""

    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError("installed bootstrap provenance must be an object")
    _fields(
        value,
        expected={
            "consumer_config_sha256",
            "webapp_fi_source_attestation_public_key_base64",
            "webapp_fi_controller_authorization_public_key_base64",
        },
        field="installed bootstrap provenance",
    )
    consumer_config_sha256 = _require_sha256(
        value.get("consumer_config_sha256"),
        field="installed bootstrap provenance consumer_config_sha256",
    )
    webapp_fi_source_attestation_public_key = _decode_exact_public_key(
        value.get("webapp_fi_source_attestation_public_key_base64"),
        field="installed bootstrap provenance WebApp-FI source attestation public key",
    )
    webapp_fi_controller_authorization_public_key = _decode_exact_public_key(
        value.get("webapp_fi_controller_authorization_public_key_base64"),
        field="installed bootstrap provenance WebApp-FI controller authorization public key",
    )
    return {
        "consumer_config_sha256": consumer_config_sha256,
        "webapp_fi_source_attestation_public_key_base64": base64.b64encode(
            webapp_fi_source_attestation_public_key
        ).decode("ascii"),
        "webapp_fi_controller_authorization_public_key_base64": base64.b64encode(
            webapp_fi_controller_authorization_public_key
        ).decode("ascii"),
    }


def load_installed_release_receipt(path: Path) -> dict[str, Any]:
    value = _read_canonical_private_json(path, field="installed release provenance receipt")
    if value.get("schema") != INSTALL_RECEIPT_SCHEMA:
        raise ReleaseProvenanceError("installed release provenance receipt schema is unsupported")
    _fields(
        value,
        expected={
            "schema",
            "status",
            "installed_at",
            "stage",
            "application",
            "control",
            "dispatcher",
            "runtime_images",
            "bootstrap_provenance",
            "receipt_sha256",
        },
        field="installed release provenance receipt",
    )
    if value.get("status") != "installed":
        raise ReleaseProvenanceError("installed release provenance receipt schema or status is unsupported")
    receipt_sha = _require_sha256(
        value.get("receipt_sha256"),
        field="installed release provenance receipt receipt_sha256",
    )
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if sha256_bytes(canonical_json_bytes(unsigned)) != receipt_sha:
        raise ReleaseProvenanceError("installed release provenance receipt hash is invalid")
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
    dispatcher = _installed_dispatcher(value.get("dispatcher"), control=control)
    bootstrap_provenance = _installed_bootstrap_provenance(value.get("bootstrap_provenance"))
    images = _runtime_contract(value.get("runtime_images"))
    runtime_images: dict[str, Any] = {
        "app_image_id": images.app_image_id,
        "image_bundle_sha256": images.image_bundle_sha256,
        "image_manifest_sha256": images.image_manifest_sha256,
    }
    if images.app_repo_digest is not None:
        runtime_images["app_repo_digest"] = images.app_repo_digest
    return {
        "stage": dict(stage),
        "application": application,
        "control": control,
        "dispatcher": dispatcher,
        "bootstrap_provenance": bootstrap_provenance,
        "runtime_images": runtime_images,
    }


def verify_installed_runtime_binding(
    receipt_path: Path,
    *,
    control_release_root: Path | None = None,
    control_release_sha: str | None = None,
    application_release_root: Path | None = None,
    application_release_sha: str | None = None,
) -> dict[str, Any]:
    """Revalidate a receipt and bind explicitly supplied runtime identities.

    The receipt is the only authority for both immutable roots.  Callers may
    supply values from a root-only config or systemd environment, but those
    values never select a root on their own: they must exactly equal the
    already verified receipt identity.
    """

    installed = load_installed_release_receipt(receipt_path)
    checks = (
        ("control", "release_root", control_release_root, "expected control release root"),
        ("control", "release_sha", control_release_sha, "expected control release SHA"),
        ("application", "release_root", application_release_root, "expected application release root"),
        ("application", "release_sha", application_release_sha, "expected application release SHA"),
    )
    for role, field, expected, label in checks:
        if expected is None:
            continue
        if field == "release_root":
            value = str(_safe_path(str(expected), field=label))
        else:
            value = _require_sha(expected, field=label)
        if installed[role][field] != value:
            raise ReleaseProvenanceError(f"installed receipt does not bind the {label}")
    return installed


def _trusted_dispatcher() -> Path:
    try:
        actual = Path(__file__).resolve(strict=True)
    except OSError as exc:
        raise ReleaseProvenanceError("cannot resolve trusted control dispatcher") from exc
    if actual != TRUSTED_DISPATCHER_PATH:
        raise ReleaseProvenanceError("receipt-bound control dispatcher must run from the fixed dispatcher path")
    return _require_file(
        TRUSTED_DISPATCHER_PATH,
        field="trusted control dispatcher",
        private=False,
        maximum=4 * 1024 * 1024,
    )


def _fixed_python() -> Path:
    try:
        python = PYTHON_BINARY.resolve(strict=True)
    except OSError as exc:
        raise ReleaseProvenanceError("cannot resolve fixed Python interpreter") from exc
    python = _require_file(
        python,
        field="fixed Python interpreter",
        private=False,
        maximum=100 * 1024 * 1024,
    )
    if not os.access(python, os.X_OK):
        raise ReleaseProvenanceError("fixed Python interpreter is not executable")
    return python


def exec_receipt_bound_control(
    *,
    receipt_path: Path,
    control_release_root: Path,
    control_release_sha: str,
    target: str,
    config_path: Path,
) -> None:
    """Exec only a fixed target from the receipt-bound immutable control root.

    This is intentionally invoked through the fixed dispatcher installed by
    the trusted preflight helper. It validates the root selected by the
    root-only systemd environment before opening any target code below that
    root, then replaces itself with a fixed Python invocation and a scrubbed
    environment.
    """

    _trusted_dispatcher()
    root = _safe_path(str(control_release_root), field="expected control release root")
    sha = _require_sha(control_release_sha, field="expected control release SHA")
    config = _safe_path(str(config_path), field="fixed control target config")
    _require_file(config, field="fixed control target config", private=True, maximum=MAX_JSON_BYTES)
    verify_installed_runtime_binding(
        receipt_path,
        control_release_root=root,
        control_release_sha=sha,
    )
    if target not in CONTROL_DISPATCH_TARGETS:
        raise ReleaseProvenanceError("control dispatcher target is not allowed")
    relative_script, fixed_arguments = CONTROL_DISPATCH_TARGETS[target]
    script = root / relative_script
    _require_file(
        script,
        field="receipt-bound control target",
        private=False,
        maximum=4 * 1024 * 1024,
    )
    command = [
        str(_fixed_python()),
        "-I",
        "-B",
        str(script),
        "--config",
        str(config),
        *fixed_arguments,
    ]
    environment = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    try:
        os.execve(str(_fixed_python()), command, environment)
    except OSError as exc:
        raise ReleaseProvenanceError("cannot exec receipt-bound control target") from exc
    raise ReleaseProvenanceError("receipt-bound control target unexpectedly returned")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build-control", help="prepare only a control bundle bound to a verified app preparation receipt")
    build.add_argument("--application-preparation-receipt", type=Path, required=True)
    build.add_argument("--control-repository", type=Path, required=True)
    build.add_argument("--control-release-sha", required=True)
    build.add_argument("--output-directory", type=Path, required=True)
    build.add_argument("--app-image-id", required=True)
    build.add_argument("--expected-alembic-revision", required=True)
    build.add_argument("--canonical-release-tree-sha256", required=True)
    build.add_argument("--webapp-fi-source-provenance-input", type=Path, required=True)
    build.add_argument("--app-repo-digest", required=False)
    install = commands.add_parser("install", help="install fresh application/control roots from one verified stage candidate")
    install.add_argument("--stage-receipt", type=Path, required=True)
    install.add_argument("--bootstrap-receipt", type=Path, required=True)
    install.add_argument("--receipt", type=Path, required=True)
    verify = commands.add_parser("verify-installed", help="revalidate the create-only receipt and immutable Git roots")
    verify.add_argument("--receipt", type=Path, required=True)
    dispatch = commands.add_parser(
        "exec-bound-control",
        help="from the fixed trusted tooling path, exec one receipt-bound WA-IR control target",
    )
    dispatch.add_argument("--receipt", type=Path, required=True)
    dispatch.add_argument("--control-release-root", type=Path, required=True)
    dispatch.add_argument("--control-release-sha", required=True)
    dispatch.add_argument("--target", choices=sorted(CONTROL_DISPATCH_TARGETS), required=True)
    dispatch.add_argument("--config", type=Path, required=True)
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
                expected_alembic_revision=args.expected_alembic_revision,
                canonical_release_tree_sha256=args.canonical_release_tree_sha256,
                webapp_fi_source_provenance_input=args.webapp_fi_source_provenance_input,
                app_repo_digest=args.app_repo_digest,
            )
        elif args.command == "install":
            result = install_release_roots(
                stage_receipt_path=args.stage_receipt,
                bootstrap_receipt_path=args.bootstrap_receipt,
                receipt_path=args.receipt,
            )
        elif args.command == "verify-installed":
            result = load_installed_release_receipt(args.receipt)
        elif args.command == "exec-bound-control":
            exec_receipt_bound_control(
                receipt_path=args.receipt,
                control_release_root=args.control_release_root,
                control_release_sha=args.control_release_sha,
                target=args.target,
                config_path=args.config,
            )
            raise ReleaseProvenanceError("receipt-bound control target unexpectedly returned")
        else:  # pragma: no cover - argparse keeps this unreachable.
            raise ReleaseProvenanceError("unsupported command")
    except ReleaseProvenanceError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
