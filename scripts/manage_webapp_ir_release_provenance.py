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
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # pragma: no cover - deployment requirements already include cryptography.
    InvalidSignature = None  # type: ignore[assignment,misc]
    serialization = None  # type: ignore[assignment]
    Ed25519PublicKey = None  # type: ignore[assignment,misc]


def _load_image_archive_contract() -> Any:
    """Load the pure archive-tag contract from the receipt-bound control code."""

    module_name = "webapp_ir_image_archive_contract"
    try:
        return __import__(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
    module_path = Path(__file__).with_name(module_name + ".py")
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
PROVENANCE_SCHEMA = "gold-trade-wa-ir-release-provenance-v2"
# These are independent FI source-side statements.  The release provenance
# embeds their complete signed payloads rather than reducing them to a few
# controller-selected claims.
WEBAPP_FI_SOURCE_ROLE_ATTESTATION_SCHEMA = "gold-trade-webapp-fi-source-role-attestation-v1"
WEBAPP_FI_SOURCE_ROLE_ATTESTATION_DOMAIN = b"gold-trade-webapp-fi-source-role-attestation-v1\x00"
WEBAPP_FI_IMAGE_EXPORT_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-image-export-receipt-v1"
WEBAPP_FI_IMAGE_EXPORT_RECEIPT_DOMAIN = b"gold-trade-webapp-fi-source-image-export-v1\x00"
WEBAPP_FI_CONTROLLER_IMAGE_ADOPTION_RECEIPT_SCHEMA = "gold-trade-webapp-fi-controller-image-adoption-receipt-v1"
WEBAPP_FI_SOURCE_SIGNING_ALGORITHM = "ed25519"
INSTALL_RECEIPT_SCHEMA = "gold-trade-wa-ir-release-provenance-install-receipt-v1"
STAGE_RECEIPT_SCHEMA = "gold-trade-wa-ir-artifact-stage-receipt-v1"
BOOTSTRAP_RECEIPT_SCHEMA = "gold-trade-wa-ir-stage-bootstrap-receipt-v1"
BOOTSTRAP_RECEIPT_NAME = "bootstrap-receipt.json"
ARTIFACT_STAGE_CONSUMER_CONFIG_SCHEMA = "gold-trade-wa-ir-artifact-stage-config-v2"

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
        "core/standby_snapshot_capacity.py",
        "scripts/webapp_ir_image_archive_contract.py",
        "config/consumer.json",
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
MAX_FI_SOURCE_PROOF_BYTES = 8 * 1024 * 1024
MAX_PROVENANCE_BYTES = (2 * MAX_FI_SOURCE_PROOF_BYTES) + MAX_JSON_BYTES
MAX_ARTIFACT_BYTES = 100 * 1024 * 1024 * 1024
MAX_BOOTSTRAP_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_BOOTSTRAP_CIPHERTEXT_BYTES = MAX_BOOTSTRAP_ARCHIVE_BYTES + 2 * 1024 * 1024

SHA_RE = re.compile(r"^[a-f0-9]{40,64}$")
BOOTSTRAP_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
# Match the preparation primitive's accepted immutable Docker repo-digest
# syntax.  Registry ports and mixed-case registry names are valid inputs and
# are only carried as verified manifest data here; they are never executed.
IMAGE_DIGEST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,511}@sha256:[a-f0-9]{64}$")
IMAGE_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,511}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
ARTIFACT_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
SITE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
OBJECT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/=-]{2,1023}$")
FI_KEY_ID_RE = re.compile(r"^ed25519-sha256:[a-f0-9]{64}$")
FI_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")


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
class WebAppFiSourceAttestation:
    """The complete FI-signed, read-only source-role attestation."""

    payload: dict[str, Any]
    sha256: str
    key_id: str
    campaign_id: str
    release_sha: str
    release_tree: str
    image_id: str
    image_reference: str
    repo_digests: tuple[str, ...]
    canonical_release_tree_sha256: str


@dataclass(frozen=True)
class WebAppFiImageExportReceipt:
    """The complete FI-signed result of exporting the attested app image."""

    payload: dict[str, Any]
    sha256: str
    key_id: str
    campaign_id: str
    release_sha: str
    release_tree: str
    image_id: str
    image_reference: str
    archive_sha256: str
    archive_bytes: int


@dataclass(frozen=True)
class ControllerImageAdoptionReceipt:
    """A controller-local, URL-free receipt for the returned immutable archive."""

    payload: dict[str, Any]
    sha256: str
    campaign_id: str
    release_sha: str
    release_tree: str
    source_attestation_sha256: str
    image_export_receipt_sha256: str
    source_signing_key_id: str
    image_id: str
    image_reference: str
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    plaintext_sha256: str
    plaintext_bytes: int


@dataclass(frozen=True)
class WebAppFiSourceProof:
    """The three immutable records that bind FI source to controller adoption."""

    source_role_attestation: WebAppFiSourceAttestation
    image_export_receipt: WebAppFiImageExportReceipt
    controller_image_adoption_receipt: ControllerImageAdoptionReceipt


@dataclass(frozen=True)
class Provenance:
    campaign_id: str
    application: ReleaseIdentity
    control: ReleaseIdentity
    runtime_images: RuntimeImageContract
    webapp_fi_source_proof: WebAppFiSourceProof


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
    webapp_fi_source_attestation_public_key: bytes


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


def _read_canonical_private_json(path: Path, *, field: str, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
    """Read a root-only receipt whose self-hash has one unambiguous encoding."""

    raw = _secure_read(path, field=field, private=True, maximum=maximum)
    value = _strict_json(raw, field=field)
    if raw != canonical_json_bytes(value) + b"\n":
        raise ReleaseProvenanceError(f"{field} must use canonical JSON")
    return value


def _require_ed25519_backend() -> None:
    if Ed25519PublicKey is None or InvalidSignature is None:
        raise ReleaseProvenanceError("Ed25519 verification support is unavailable")


def _decode_exact_base64(value: object, *, field: str, expected_bytes: int) -> bytes:
    if not isinstance(value, str) or not value:
        raise ReleaseProvenanceError(f"{field} is invalid")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ReleaseProvenanceError(f"{field} must be strict base64") from exc
    if len(decoded) != expected_bytes:
        raise ReleaseProvenanceError(f"{field} has an unsafe length")
    return decoded


def load_webapp_fi_source_attestation_public_key(path: Path) -> bytes:
    """Read the independent, root-only FI source-attestation verification key."""

    raw = _secure_read(path, field="webapp_fi_source_attestation_public_key", private=True, maximum=32)
    if len(raw) != 32:
        raise ReleaseProvenanceError("webapp_fi_source_attestation_public_key must contain exactly 32 raw bytes")
    return raw


def _require_campaign_id(value: object, *, field: str) -> str:
    try:
        return image_contract.require_campaign_id(value, field=field)
    except image_contract.ImageArchiveContractError as exc:
        raise ReleaseProvenanceError(f"{field} is invalid") from exc


def _canonical_payload_sha256(value: Mapping[str, Any]) -> str:
    """Match the FI create-only JSON convention, including its final newline."""

    return sha256_bytes(canonical_json_bytes(value) + b"\n")


def _reject_persisted_url(value: Mapping[str, Any], *, field: str) -> None:
    """Neither proof nor adoption receipt may retain a presigned transport URL."""

    encoded = canonical_json_bytes(value).lower()
    if b"https://" in encoded or b"http://" in encoded or b"presigned" in encoded or b'"url"' in encoded:
        raise ReleaseProvenanceError(f"{field} persists a forbidden URL")


def _require_timestamp(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReleaseProvenanceError(f"{field} is invalid")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseProvenanceError(f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise ReleaseProvenanceError(f"{field} is invalid")
    return value


def _require_positive_size(value: object, *, field: str, maximum: int = MAX_ARTIFACT_BYTES) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ReleaseProvenanceError(f"{field} is invalid")
    return value


def _require_version_id(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ReleaseProvenanceError(f"{field} is invalid")
    return value


def _require_object_key(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not OBJECT_KEY_RE.fullmatch(value):
        raise ReleaseProvenanceError(f"{field} is invalid")
    return value


def _require_reference_list(
    value: object,
    *,
    field: str,
    pattern: re.Pattern[str],
) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and pattern.fullmatch(item) for item in value):
        raise ReleaseProvenanceError(f"{field} is invalid")
    if len(set(value)) != len(value) or value != sorted(value):
        raise ReleaseProvenanceError(f"{field} is not deterministically sorted")
    return tuple(value)


def _require_pinned_fi_public_key(value: object, *, field: str, public_key: bytes) -> str:
    encoded = value
    decoded = _decode_exact_base64(encoded, field=field, expected_bytes=32)
    if decoded != public_key:
        raise ReleaseProvenanceError(f"{field} does not match the pinned FI key")
    assert isinstance(encoded, str)
    return encoded


def _fi_key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + sha256_bytes(public_key)


def _require_fi_key_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not FI_KEY_ID_RE.fullmatch(value):
        raise ReleaseProvenanceError(f"{field} is invalid")
    return value


def _require_fi_commit(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not FI_COMMIT_RE.fullmatch(value):
        raise ReleaseProvenanceError(f"{field} is invalid")
    return value


def _verify_fi_signature(
    payload: Mapping[str, Any],
    *,
    field: str,
    public_key: bytes,
    domain: bytes,
) -> str:
    if len(public_key) != 32:
        raise ReleaseProvenanceError(f"{field} public key has an unsafe length")
    _require_pinned_fi_public_key(
        payload.get("source_signing_public_key_base64"),
        field=f"{field} signing public key",
        public_key=public_key,
    )
    key_id = _require_fi_key_id(payload.get("source_signing_key_id"), field=f"{field} signing key ID")
    if key_id != _fi_key_id(public_key):
        raise ReleaseProvenanceError(f"{field} signing key does not match the pinned FI key")
    signature = payload.get("source_signature")
    if not isinstance(signature, Mapping):
        raise ReleaseProvenanceError(f"{field} signature is invalid")
    _fields(signature, expected={"algorithm", "signature_base64"}, field=f"{field} signature")
    if signature.get("algorithm") != WEBAPP_FI_SOURCE_SIGNING_ALGORITHM:
        raise ReleaseProvenanceError(f"{field} signature algorithm is invalid")
    raw_signature = _decode_exact_base64(
        signature.get("signature_base64"),
        field=f"{field} signature",
        expected_bytes=64,
    )
    _require_ed25519_backend()
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            raw_signature,
            domain + canonical_json_bytes({key: item for key, item in payload.items() if key != "source_signature"}),
        )
    except InvalidSignature as exc:
        raise ReleaseProvenanceError(f"{field} signature verification failed") from exc
    except ValueError as exc:  # pragma: no cover - fixed-length key was checked above.
        raise ReleaseProvenanceError(f"{field} public key is invalid") from exc
    return key_id


def _source_adoption_delivery(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError(f"{field} is invalid")
    _fields(
        value,
        expected={
            "object_key",
            "version_id",
            "ciphertext_sha256",
            "ciphertext_bytes",
            "plaintext_sha256",
            "plaintext_bytes",
            "delivery_envelope_sha256",
            "controller_public_key_base64",
        },
        field=field,
    )
    _require_object_key(value.get("object_key"), field=f"{field} object_key")
    _require_version_id(value.get("version_id"), field=f"{field} version_id")
    for name in ("ciphertext_sha256", "plaintext_sha256", "delivery_envelope_sha256"):
        _require_sha256(value.get(name), field=f"{field} {name}")
    for name in ("ciphertext_bytes", "plaintext_bytes"):
        _require_positive_size(value.get(name), field=f"{field} {name}", maximum=MAX_FI_SOURCE_PROOF_BYTES + MAX_BOOTSTRAP_CIPHERTEXT_BYTES)
    _decode_exact_base64(value.get("controller_public_key_base64"), field=f"{field} controller public key", expected_bytes=32)
    return dict(value)


def _runtime_projection_record(
    value: object,
    *,
    field: str,
    release_sha: str,
    release_tree: str,
    canonical_release_tree_sha256: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError(f"{field} is invalid")
    _fields(
        value,
        expected={"runtime_source_root", "release_sha", "git_tree", "descriptor_sha256", "projections", "projection_sha256"},
        field=field,
    )
    runtime_source_root = value.get("runtime_source_root")
    if not isinstance(runtime_source_root, str) or not PATH_RE.fullmatch(runtime_source_root):
        raise ReleaseProvenanceError(f"{field} runtime_source_root is invalid")
    if value.get("release_sha") != release_sha or value.get("git_tree") != release_tree:
        raise ReleaseProvenanceError(f"{field} release binding is invalid")
    if value.get("descriptor_sha256") != canonical_release_tree_sha256:
        raise ReleaseProvenanceError(f"{field} descriptor binding is invalid")
    projections = value.get("projections")
    if not isinstance(projections, Mapping) or not projections:
        raise ReleaseProvenanceError(f"{field} projections are invalid")
    projection_sha256 = _require_sha256(value.get("projection_sha256"), field=f"{field} projection_sha256")
    if projection_sha256 != sha256_bytes(canonical_json_bytes(projections)):
        raise ReleaseProvenanceError(f"{field} projection_sha256 is invalid")
    return dict(value)


def _static_assets_record(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError(f"{field} is invalid")
    _fields(
        value,
        expected={"descriptor_sha256", "artifact", "files_sha256", "file_count", "source_kind"},
        field=field,
    )
    _require_sha256(value.get("descriptor_sha256"), field=f"{field} descriptor_sha256")
    _require_sha256(value.get("files_sha256"), field=f"{field} files_sha256")
    if value.get("source_kind") != "deterministic_2c08_dist_manifest":
        raise ReleaseProvenanceError(f"{field} source_kind is invalid")
    if isinstance(value.get("file_count"), bool) or not isinstance(value.get("file_count"), int) or value["file_count"] < 0:
        raise ReleaseProvenanceError(f"{field} file_count is invalid")
    artifact = value.get("artifact")
    if not isinstance(artifact, Mapping):
        raise ReleaseProvenanceError(f"{field} artifact is invalid")
    _fields(
        artifact,
        expected={"object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes"},
        field=f"{field} artifact",
    )
    _require_object_key(artifact.get("object_key"), field=f"{field} artifact object_key")
    _require_version_id(artifact.get("version_id"), field=f"{field} artifact version_id")
    for name in ("ciphertext_sha256", "plaintext_sha256"):
        _require_sha256(artifact.get(name), field=f"{field} artifact {name}")
    for name in ("ciphertext_bytes", "plaintext_bytes"):
        _require_positive_size(artifact.get(name), field=f"{field} artifact {name}")
    return dict(value)


def _role_attestation(value: object, *, public_key: bytes) -> WebAppFiSourceAttestation:
    """Verify the full FI-signed source-role proof without discarding its evidence."""

    field = "webapp_fi source role attestation"
    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError(f"{field} must be an object")
    payload = dict(value)
    _reject_persisted_url(payload, field=field)
    _fields(
        payload,
        expected={
            "schema",
            "status",
            "attested_at",
            "campaign_id",
            "source_site",
            "destination_site",
            "package_id",
            "application",
            "tooling",
            "source_adoption_install_receipt_sha256",
            "source_adoption_delivery",
            "canonical_release_tree_sha256",
            "application_release_tree",
            "source_signer_enrollment",
            "runtime_projection",
            "static_assets_proof",
            "containers",
            "active_application_image",
            "schema_observation",
            "race_check",
            "snapshot_transport",
            "source_signing_public_key_base64",
            "source_signing_key_id",
            "source_signature",
        },
        field=field,
    )
    if (
        payload.get("schema") != WEBAPP_FI_SOURCE_ROLE_ATTESTATION_SCHEMA
        or payload.get("status") != "attested"
        or payload.get("source_site") != STAGE_SOURCE_SITE
        or payload.get("destination_site") != STAGE_DESTINATION_SITE
    ):
        raise ReleaseProvenanceError(f"{field} schema or site binding is invalid")
    _require_timestamp(payload.get("attested_at"), field=f"{field} attested_at")
    campaign_id = _require_campaign_id(payload.get("campaign_id"), field=f"{field} campaign_id")
    package_id = payload.get("package_id")
    if not isinstance(package_id, str) or not BUNDLE_ID_RE.fullmatch(package_id):
        raise ReleaseProvenanceError(f"{field} package_id is invalid")
    application = payload.get("application")
    if not isinstance(application, Mapping):
        raise ReleaseProvenanceError(f"{field} application is invalid")
    _fields(application, expected={"release_sha", "expected_alembic_revision"}, field=f"{field} application")
    release_sha = _require_fi_commit(application.get("release_sha"), field=f"{field} release_sha")
    revision = application.get("expected_alembic_revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{12}", revision):
        raise ReleaseProvenanceError(f"{field} expected_alembic_revision is invalid")
    tooling = payload.get("tooling")
    if not isinstance(tooling, Mapping):
        raise ReleaseProvenanceError(f"{field} tooling is invalid")
    _fields(tooling, expected={"control_commit", "control_tree"}, field=f"{field} tooling")
    _require_fi_commit(tooling.get("control_commit"), field=f"{field} tooling control_commit")
    _require_fi_commit(tooling.get("control_tree"), field=f"{field} tooling control_tree")
    _require_sha256(payload.get("source_adoption_install_receipt_sha256"), field=f"{field} source adoption install receipt")
    _source_adoption_delivery(payload.get("source_adoption_delivery"), field=f"{field} source adoption delivery")
    canonical_release_tree_sha256 = _require_sha256(
        payload.get("canonical_release_tree_sha256"),
        field=f"{field} canonical_release_tree_sha256",
    )
    release_tree = _require_fi_commit(payload.get("application_release_tree"), field=f"{field} application_release_tree")
    enrollment = payload.get("source_signer_enrollment")
    if not isinstance(enrollment, Mapping):
        raise ReleaseProvenanceError(f"{field} signer enrollment is invalid")
    _fields(
        enrollment,
        expected={
            "receipt_sha256",
            "certificate_sha256",
            "fi_ssh_host_public_key_sha256",
            "controller_public_key_base64",
            "source_signing_public_key_base64",
        },
        field=f"{field} signer enrollment",
    )
    for name in ("receipt_sha256", "certificate_sha256", "fi_ssh_host_public_key_sha256"):
        _require_sha256(enrollment.get(name), field=f"{field} signer enrollment {name}")
    _decode_exact_base64(enrollment.get("controller_public_key_base64"), field=f"{field} controller public key", expected_bytes=32)
    _require_pinned_fi_public_key(
        enrollment.get("source_signing_public_key_base64"),
        field=f"{field} signer enrollment key",
        public_key=public_key,
    )
    projection = payload.get("runtime_projection")
    if not isinstance(projection, Mapping):
        raise ReleaseProvenanceError(f"{field} runtime_projection is invalid")
    _fields(projection, expected={"before", "after"}, field=f"{field} runtime_projection")
    before_projection = _runtime_projection_record(
        projection.get("before"),
        field=f"{field} runtime projection before",
        release_sha=release_sha,
        release_tree=release_tree,
        canonical_release_tree_sha256=canonical_release_tree_sha256,
    )
    after_projection = _runtime_projection_record(
        projection.get("after"),
        field=f"{field} runtime projection after",
        release_sha=release_sha,
        release_tree=release_tree,
        canonical_release_tree_sha256=canonical_release_tree_sha256,
    )
    if before_projection != after_projection:
        raise ReleaseProvenanceError(f"{field} runtime projection race proof is invalid")
    static_assets = payload.get("static_assets_proof")
    if not isinstance(static_assets, Mapping):
        raise ReleaseProvenanceError(f"{field} static_assets_proof is invalid")
    _fields(
        static_assets,
        expected={"before", "after", "proof_is_not_static_payload", "promotion_requires_verified_immutable_age_object"},
        field=f"{field} static_assets_proof",
    )
    if (
        static_assets.get("proof_is_not_static_payload") is not True
        or static_assets.get("promotion_requires_verified_immutable_age_object") is not True
    ):
        raise ReleaseProvenanceError(f"{field} static asset policy is invalid")
    if _static_assets_record(static_assets.get("before"), field=f"{field} static assets before") != _static_assets_record(
        static_assets.get("after"), field=f"{field} static assets after"
    ):
        raise ReleaseProvenanceError(f"{field} static asset race proof is invalid")
    containers = payload.get("containers")
    if not isinstance(containers, Mapping) or set(containers) != {"database", "application", "sync_worker"}:
        raise ReleaseProvenanceError(f"{field} containers are invalid")
    active = payload.get("active_application_image")
    if not isinstance(active, Mapping):
        raise ReleaseProvenanceError(f"{field} active_application_image is invalid")
    _fields(active, expected={"image_id", "image_reference", "repo_tags", "repo_digests"}, field=f"{field} active_application_image")
    image_id = active.get("image_id")
    image_reference = active.get("image_reference")
    if not isinstance(image_id, str) or not IMAGE_ID_RE.fullmatch(image_id):
        raise ReleaseProvenanceError(f"{field} active image ID is invalid")
    if not isinstance(image_reference, str) or not IMAGE_REFERENCE_RE.fullmatch(image_reference):
        raise ReleaseProvenanceError(f"{field} active image reference is invalid")
    repo_tags = _require_reference_list(active.get("repo_tags"), field=f"{field} active repo_tags", pattern=IMAGE_REFERENCE_RE)
    repo_digests = _require_reference_list(active.get("repo_digests"), field=f"{field} active repo_digests", pattern=IMAGE_DIGEST_RE)
    if image_reference not in set(repo_tags) | set(repo_digests):
        raise ReleaseProvenanceError(f"{field} active image reference is not recorded")
    for name in ("application", "sync_worker"):
        container = containers.get(name)
        if not isinstance(container, Mapping) or container.get("image_id") != image_id or container.get("image_reference") != image_reference:
            raise ReleaseProvenanceError(f"{field} {name} container is not bound to the active image")
    if payload.get("schema_observation") != {
        "observed_alembic_revision": revision,
        "capture_role_verified_read_only": True,
    }:
        raise ReleaseProvenanceError(f"{field} schema observation is invalid")
    if payload.get("race_check") != {
        "runtime_projection_unchanged": True,
        "static_assets_unchanged": True,
        "application_container_unchanged": True,
        "sync_worker_container_unchanged": True,
        "database_container_unchanged": True,
        "active_image_unchanged": True,
        "schema_unchanged": True,
    }:
        raise ReleaseProvenanceError(f"{field} race_check is invalid")
    if payload.get("snapshot_transport") != {
        "payload_path": "private_versioned_object_storage_age_only",
        "one_off_publication_only": True,
        "direct_webapp_fi_to_webapp_ir_transfer": False,
        "automatic_deletion": False,
    }:
        raise ReleaseProvenanceError(f"{field} snapshot transport is invalid")
    key_id = _verify_fi_signature(
        payload,
        field=field,
        public_key=public_key,
        domain=WEBAPP_FI_SOURCE_ROLE_ATTESTATION_DOMAIN,
    )
    return WebAppFiSourceAttestation(
        payload=payload,
        sha256=_canonical_payload_sha256(payload),
        key_id=key_id,
        campaign_id=campaign_id,
        release_sha=release_sha,
        release_tree=release_tree,
        image_id=image_id,
        image_reference=image_reference,
        repo_digests=repo_digests,
        canonical_release_tree_sha256=canonical_release_tree_sha256,
    )


def _image_export_receipt(
    value: object,
    *,
    public_key: bytes,
    attestation: WebAppFiSourceAttestation,
) -> WebAppFiImageExportReceipt:
    field = "webapp_fi image export receipt"
    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError(f"{field} must be an object")
    payload = dict(value)
    _reject_persisted_url(payload, field=field)
    _fields(
        payload,
        expected={
            "schema",
            "status",
            "exported_at",
            "export_id",
            "campaign_id",
            "source_site",
            "destination_site",
            "application",
            "application_release_tree",
            "tooling",
            "canonical_release_tree_sha256",
            "source_role_attestation_sha256",
            "image",
            "pre_export_runtime",
            "post_export_runtime",
            "image_archive_does_not_prove_bind_mounted_runtime",
            "archive_consumption",
            "object_storage_export_required",
            "source_signing_public_key_base64",
            "source_signing_key_id",
            "source_signature",
        },
        field=field,
    )
    if (
        payload.get("schema") != WEBAPP_FI_IMAGE_EXPORT_RECEIPT_SCHEMA
        or payload.get("status") != "exported"
        or payload.get("source_site") != STAGE_SOURCE_SITE
        or payload.get("destination_site") != STAGE_DESTINATION_SITE
    ):
        raise ReleaseProvenanceError(f"{field} schema or site binding is invalid")
    _require_timestamp(payload.get("exported_at"), field=f"{field} exported_at")
    export_id = payload.get("export_id")
    if not isinstance(export_id, str) or not BUNDLE_ID_RE.fullmatch(export_id):
        raise ReleaseProvenanceError(f"{field} export_id is invalid")
    campaign_id = _require_campaign_id(payload.get("campaign_id"), field=f"{field} campaign_id")
    application = payload.get("application")
    if application != attestation.payload["application"]:
        raise ReleaseProvenanceError(f"{field} application does not match the source role attestation")
    tooling = payload.get("tooling")
    if tooling != attestation.payload["tooling"]:
        raise ReleaseProvenanceError(f"{field} tooling does not match the source role attestation")
    if (
        campaign_id != attestation.campaign_id
        or payload.get("application_release_tree") != attestation.release_tree
        or payload.get("canonical_release_tree_sha256") != attestation.canonical_release_tree_sha256
    ):
        raise ReleaseProvenanceError(f"{field} campaign or tree descriptor does not match the source role attestation")
    if payload.get("source_role_attestation_sha256") != attestation.sha256:
        raise ReleaseProvenanceError(f"{field} does not bind the source role attestation")
    image = payload.get("image")
    if not isinstance(image, Mapping):
        raise ReleaseProvenanceError(f"{field} image is invalid")
    _fields(
        image,
        expected={"image_id", "image_reference", "archive_sha256", "archive_bytes", "docker_manifest_sha256", "docker_config_sha256", "layer_count", "repo_tags"},
        field=f"{field} image",
    )
    image_id = image.get("image_id")
    image_reference = image.get("image_reference")
    if image_id != attestation.image_id or image_reference != attestation.image_reference:
        raise ReleaseProvenanceError(f"{field} image does not match the source role attestation")
    archive_sha256 = _require_sha256(image.get("archive_sha256"), field=f"{field} image archive_sha256")
    archive_bytes = _require_positive_size(image.get("archive_bytes"), field=f"{field} image archive_bytes")
    _require_sha256(image.get("docker_manifest_sha256"), field=f"{field} image docker_manifest_sha256")
    _require_sha256(image.get("docker_config_sha256"), field=f"{field} image docker_config_sha256")
    if isinstance(image.get("layer_count"), bool) or not isinstance(image.get("layer_count"), int) or image["layer_count"] < 1:
        raise ReleaseProvenanceError(f"{field} image layer_count is invalid")
    _require_reference_list(image.get("repo_tags"), field=f"{field} image repo_tags", pattern=IMAGE_REFERENCE_RE)
    for name in ("pre_export_runtime", "post_export_runtime"):
        runtime = payload.get(name)
        if not isinstance(runtime, Mapping):
            raise ReleaseProvenanceError(f"{field} {name} is invalid")
        _fields(runtime, expected={"application", "sync_worker", "active_image"}, field=f"{field} {name}")
        if (
            runtime.get("application") != attestation.payload["containers"]["application"]
            or runtime.get("sync_worker") != attestation.payload["containers"]["sync_worker"]
            or runtime.get("active_image") != attestation.payload["active_application_image"]
        ):
            raise ReleaseProvenanceError(f"{field} {name} does not match the source role attestation")
    if payload.get("image_archive_does_not_prove_bind_mounted_runtime") is not True:
        raise ReleaseProvenanceError(f"{field} mounted-runtime limitation is missing")
    if payload.get("archive_consumption") != {
        "docker_load_prohibited": True,
        "fi_local_archive_verification_before_age_encryption": True,
        "controller_read_back_verification_after_age_encryption": True,
        "raw_repo_tags_are_not_authorization": True,
    }:
        raise ReleaseProvenanceError(f"{field} archive consumption policy is invalid")
    if payload.get("object_storage_export_required") != {
        "transport": "private_versioned_age_only",
        "create_only": True,
        "read_back_same_version_id": True,
        "direct_webapp_fi_to_webapp_ir_transfer": False,
    }:
        raise ReleaseProvenanceError(f"{field} object storage transport policy is invalid")
    key_id = _verify_fi_signature(
        payload,
        field=field,
        public_key=public_key,
        domain=WEBAPP_FI_IMAGE_EXPORT_RECEIPT_DOMAIN,
    )
    if key_id != attestation.key_id:
        raise ReleaseProvenanceError(f"{field} signing key does not match the source role attestation")
    return WebAppFiImageExportReceipt(
        payload=payload,
        sha256=_canonical_payload_sha256(payload),
        key_id=key_id,
        campaign_id=campaign_id,
        release_sha=attestation.release_sha,
        release_tree=attestation.release_tree,
        image_id=attestation.image_id,
        image_reference=attestation.image_reference,
        archive_sha256=archive_sha256,
        archive_bytes=archive_bytes,
    )


def _controller_image_adoption_receipt(
    value: object,
    *,
    attestation: WebAppFiSourceAttestation,
    image_export: WebAppFiImageExportReceipt,
) -> ControllerImageAdoptionReceipt:
    """Validate only controller-local, URL-free evidence for the return object."""

    field = "webapp_fi controller image adoption receipt"
    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError(f"{field} must be an object")
    payload = dict(value)
    _reject_persisted_url(payload, field=field)
    _fields(
        payload,
        expected={
            "schema",
            "status",
            "adopted_at",
            "campaign_id",
            "source_site",
            "destination_site",
            "application",
            "source_role_attestation_sha256",
            "image_export_receipt_sha256",
            "source_signing_key_id",
            "image",
            "object",
            "read_back_verified",
            "decrypted_verified",
        },
        field=field,
    )
    if (
        payload.get("schema") != WEBAPP_FI_CONTROLLER_IMAGE_ADOPTION_RECEIPT_SCHEMA
        or payload.get("status") != "adopted"
        or payload.get("source_site") != STAGE_SOURCE_SITE
        or payload.get("destination_site") != STAGE_DESTINATION_SITE
    ):
        raise ReleaseProvenanceError(f"{field} schema or site binding is invalid")
    _require_timestamp(payload.get("adopted_at"), field=f"{field} adopted_at")
    campaign_id = _require_campaign_id(payload.get("campaign_id"), field=f"{field} campaign_id")
    application = payload.get("application")
    if not isinstance(application, Mapping):
        raise ReleaseProvenanceError(f"{field} application is invalid")
    _fields(application, expected={"release_sha", "release_tree"}, field=f"{field} application")
    release_sha = _require_fi_commit(application.get("release_sha"), field=f"{field} release_sha")
    release_tree = _require_fi_commit(application.get("release_tree"), field=f"{field} release_tree")
    source_attestation_sha256 = _require_sha256(
        payload.get("source_role_attestation_sha256"), field=f"{field} source_role_attestation_sha256"
    )
    image_export_receipt_sha256 = _require_sha256(
        payload.get("image_export_receipt_sha256"), field=f"{field} image_export_receipt_sha256"
    )
    source_signing_key_id = _require_fi_key_id(payload.get("source_signing_key_id"), field=f"{field} source_signing_key_id")
    image = payload.get("image")
    if not isinstance(image, Mapping):
        raise ReleaseProvenanceError(f"{field} image is invalid")
    _fields(image, expected={"image_id", "image_reference"}, field=f"{field} image")
    image_id = image.get("image_id")
    image_reference = image.get("image_reference")
    if not isinstance(image_id, str) or not IMAGE_ID_RE.fullmatch(image_id) or not isinstance(image_reference, str) or not IMAGE_REFERENCE_RE.fullmatch(image_reference):
        raise ReleaseProvenanceError(f"{field} image is invalid")
    object_value = payload.get("object")
    if not isinstance(object_value, Mapping):
        raise ReleaseProvenanceError(f"{field} object is invalid")
    _fields(
        object_value,
        expected={"object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes"},
        field=f"{field} object",
    )
    object_key = _require_object_key(object_value.get("object_key"), field=f"{field} object_key")
    version_id = _require_version_id(object_value.get("version_id"), field=f"{field} version_id")
    ciphertext_sha256 = _require_sha256(object_value.get("ciphertext_sha256"), field=f"{field} ciphertext_sha256")
    ciphertext_bytes = _require_positive_size(object_value.get("ciphertext_bytes"), field=f"{field} ciphertext_bytes")
    plaintext_sha256 = _require_sha256(object_value.get("plaintext_sha256"), field=f"{field} plaintext_sha256")
    plaintext_bytes = _require_positive_size(object_value.get("plaintext_bytes"), field=f"{field} plaintext_bytes")
    if payload.get("read_back_verified") is not True or payload.get("decrypted_verified") is not True:
        raise ReleaseProvenanceError(f"{field} read-back or decrypt verification is missing")
    if (
        campaign_id != attestation.campaign_id
        or release_sha != attestation.release_sha
        or release_tree != attestation.release_tree
        or source_attestation_sha256 != attestation.sha256
        or image_export_receipt_sha256 != image_export.sha256
        or source_signing_key_id != attestation.key_id
        or image_id != attestation.image_id
        or image_reference != attestation.image_reference
        or plaintext_sha256 != image_export.archive_sha256
        or plaintext_bytes != image_export.archive_bytes
    ):
        raise ReleaseProvenanceError(f"{field} does not cross-bind the FI source proof")
    return ControllerImageAdoptionReceipt(
        payload=payload,
        sha256=_canonical_payload_sha256(payload),
        campaign_id=campaign_id,
        release_sha=release_sha,
        release_tree=release_tree,
        source_attestation_sha256=source_attestation_sha256,
        image_export_receipt_sha256=image_export_receipt_sha256,
        source_signing_key_id=source_signing_key_id,
        image_id=image_id,
        image_reference=image_reference,
        object_key=object_key,
        version_id=version_id,
        ciphertext_sha256=ciphertext_sha256,
        ciphertext_bytes=ciphertext_bytes,
        plaintext_sha256=plaintext_sha256,
        plaintext_bytes=plaintext_bytes,
    )


def _source_proof(value: object, *, public_key: bytes) -> WebAppFiSourceProof:
    if not isinstance(value, Mapping):
        raise ReleaseProvenanceError("webapp_fi_source_proof must be an object")
    _fields(
        value,
        expected={"source_role_attestation", "image_export_receipt", "controller_image_adoption_receipt"},
        field="webapp_fi_source_proof",
    )
    attestation = _role_attestation(value.get("source_role_attestation"), public_key=public_key)
    image_export = _image_export_receipt(
        value.get("image_export_receipt"),
        public_key=public_key,
        attestation=attestation,
    )
    adoption = _controller_image_adoption_receipt(
        value.get("controller_image_adoption_receipt"),
        attestation=attestation,
        image_export=image_export,
    )
    return WebAppFiSourceProof(
        source_role_attestation=attestation,
        image_export_receipt=image_export,
        controller_image_adoption_receipt=adoption,
    )


def load_webapp_fi_source_proof(
    *,
    source_role_attestation: Path,
    image_export_receipt: Path,
    controller_image_adoption_receipt: Path,
    public_key: bytes,
) -> WebAppFiSourceProof:
    return _source_proof(
        {
            "source_role_attestation": _read_canonical_private_json(
                source_role_attestation,
                field="webapp_fi source role attestation",
                maximum=MAX_FI_SOURCE_PROOF_BYTES,
            ),
            "image_export_receipt": _read_canonical_private_json(
                image_export_receipt,
                field="webapp_fi image export receipt",
                maximum=MAX_FI_SOURCE_PROOF_BYTES,
            ),
            "controller_image_adoption_receipt": _read_canonical_private_json(
                controller_image_adoption_receipt,
                field="webapp_fi controller image adoption receipt",
                maximum=MAX_JSON_BYTES,
            ),
        },
        public_key=public_key,
    )


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


def _source_proof_app_repo_digest(proof: WebAppFiSourceProof) -> str | None:
    """Retain the existing single-digest runtime contract without hiding aliases."""

    digests = proof.source_role_attestation.repo_digests
    if len(digests) > 1:
        raise ReleaseProvenanceError("FI source proof has more than one active application repo digest")
    return digests[0] if digests else None


def load_provenance(
    path: Path,
    *,
    must_absent: bool | None,
    webapp_fi_source_attestation_public_key: bytes,
) -> Provenance:
    value = _read_private_json(path, field="release provenance", maximum=MAX_PROVENANCE_BYTES)
    _fields(
        value,
        expected={
            "schema",
            "campaign_id",
            "application",
            "control",
            "runtime_images",
            "webapp_fi_source_proof",
        },
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
    campaign_id = _require_campaign_id(value.get("campaign_id"), field="release provenance campaign_id")
    runtime_images = _runtime_contract(value.get("runtime_images"))
    source_proof = _source_proof(
        value.get("webapp_fi_source_proof"),
        public_key=webapp_fi_source_attestation_public_key,
    )
    attestation = source_proof.source_role_attestation
    if (
        attestation.campaign_id != campaign_id
        or attestation.release_sha != application.release_sha
        or attestation.release_tree != application.tree_sha
        or attestation.image_id != runtime_images.app_image_id
        or _source_proof_app_repo_digest(source_proof) != runtime_images.app_repo_digest
    ):
        raise ReleaseProvenanceError("webapp_fi_source_proof does not bind the release provenance")
    return Provenance(
        campaign_id=campaign_id,
        application=application,
        control=control,
        runtime_images=runtime_images,
        webapp_fi_source_proof=source_proof,
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


def _bootstrap_source_attestation_public_key(
    *,
    candidate_directory: Path,
    files: Mapping[str, str],
) -> bytes:
    """Load the FI signer pin from the hash-bound bootstrap consumer config."""

    config_directory = candidate_directory / "config"
    _require_directory(config_directory, field="bootstrap consumer config directory", private=True)
    config_path = config_directory / "consumer.json"
    raw = _secure_read(
        config_path,
        field="bootstrap consumer config",
        private=True,
        maximum=MAX_JSON_BYTES,
    )
    if sha256_bytes(raw) != files["config/consumer.json"]:
        raise ReleaseProvenanceError("bootstrap consumer config hash does not match its receipt")
    config = _strict_json(raw, field="bootstrap consumer config")
    _fields(
        config,
        expected={
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
            "maximum_artifact_bytes",
        },
        field="bootstrap consumer config",
    )
    if (
        config.get("schema") != ARTIFACT_STAGE_CONSUMER_CONFIG_SCHEMA
        or config.get("source_site") != STAGE_SOURCE_SITE
    ):
        raise ReleaseProvenanceError("bootstrap consumer config source-attestation contract is invalid")
    return _decode_exact_base64(
        config.get("webapp_fi_source_attestation_public_key_base64"),
        field="bootstrap consumer config webapp_fi_source_attestation_public_key_base64",
        expected_bytes=32,
    )


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
    return BootstrapReceipt(
        path=path,
        control_commit=control_commit,
        control_tree=control_tree,
        webapp_fi_source_attestation_public_key=_bootstrap_source_attestation_public_key(
            candidate_directory=candidate_directory,
            files=files,
        ),
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


def _validate_staged_image_manifest(path: Path, provenance: Provenance) -> None:
    value = _read_private_json(path, field="staged image manifest")
    _fields(
        value,
        expected={"schema", "status", "campaign_id", "release_sha", "archive", "image_set_sha256", "images"},
        field="staged image manifest",
    )
    if (
        value.get("schema") != IMAGE_MANIFEST_SCHEMA
        or value.get("status") != "prepared"
        or value.get("release_sha") != provenance.application.release_sha
        or value.get("campaign_id") != provenance.campaign_id
    ):
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


def _verify_artifact_contract(artifacts: Mapping[str, Artifact], provenance: Provenance) -> None:
    """Verify the five normal artifacts against independently verified provenance."""

    expected_names = {
        APPLICATION_BUNDLE_ARTIFACT,
        IMAGE_BUNDLE_ARTIFACT,
        IMAGE_MANIFEST_ARTIFACT,
        CONTROL_BUNDLE_ARTIFACT,
        PROVENANCE_ARTIFACT,
    }
    if set(artifacts) != expected_names:
        raise ReleaseProvenanceError("staged candidate must contain exactly the prepared application and control artifacts")
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
    proof = provenance.webapp_fi_source_proof
    attestation = proof.source_role_attestation
    image_export = proof.image_export_receipt
    adoption = proof.controller_image_adoption_receipt
    _exact_bindings(
        artifacts[PROVENANCE_ARTIFACT],
        {
            "application_bundle_sha256": provenance.application.bundle_sha256,
            "application_release_sha": provenance.application.release_sha,
            "artifact_sha256": artifacts[PROVENANCE_ARTIFACT].sha256,
            "campaign_id": provenance.campaign_id,
            "control_bundle_sha256": provenance.control.bundle_sha256,
            "control_release_sha": provenance.control.release_sha,
            "image_manifest_sha256": provenance.runtime_images.image_manifest_sha256,
            "webapp_fi_adoption_receipt_sha256": adoption.sha256,
            "webapp_fi_attestation_key_id": attestation.key_id,
            "webapp_fi_attestation_sha256": attestation.sha256,
            "webapp_fi_image_export_sha256": image_export.sha256,
            "webapp_fi_source_archive_sha256": adoption.plaintext_sha256,
            "webapp_fi_source_archive_version_id": adoption.version_id,
            "webapp_fi_source_image_id": adoption.image_id,
        },
    )
    if image_bundle.sha256 != provenance.runtime_images.image_bundle_sha256 or manifest.sha256 != provenance.runtime_images.image_manifest_sha256:
        raise ReleaseProvenanceError("staged image artifacts do not match release provenance")
    _validate_staged_image_manifest(manifest.path, provenance)


def verify_publishable_artifacts(
    *,
    artifact_paths: Mapping[str, Path],
    artifact_bindings: Mapping[str, Mapping[str, str]],
    expected_release_sha: str,
    webapp_fi_source_attestation_public_key: bytes,
) -> Provenance:
    """Fail closed on controller inputs before the seven-object mutation begins."""

    expected_release_sha = _require_sha(expected_release_sha, field="expected application release_sha")
    expected_names = {
        APPLICATION_BUNDLE_ARTIFACT,
        IMAGE_BUNDLE_ARTIFACT,
        IMAGE_MANIFEST_ARTIFACT,
        CONTROL_BUNDLE_ARTIFACT,
        PROVENANCE_ARTIFACT,
    }
    if set(artifact_paths) != expected_names or set(artifact_bindings) != expected_names:
        raise ReleaseProvenanceError("publishable artifacts must contain exactly the five normal artifact inputs")
    artifacts: dict[str, Artifact] = {}
    for name in sorted(expected_names):
        path = artifact_paths[name]
        if not isinstance(path, Path):
            raise ReleaseProvenanceError("publishable artifact path is invalid")
        _require_file(path, field=f"publishable {name}", private=True, maximum=MAX_ARTIFACT_BYTES)
        digest, bytes_value = sha256_file(path)
        artifacts[name] = Artifact(
            name=name,
            path=path,
            sha256=digest,
            bytes=bytes_value,
            bindings=_bindings(artifact_bindings[name], field=f"publishable {name} bindings"),
        )
    provenance = load_provenance(
        artifacts[PROVENANCE_ARTIFACT].path,
        must_absent=None,
        webapp_fi_source_attestation_public_key=webapp_fi_source_attestation_public_key,
    )
    if provenance.application.release_sha != expected_release_sha:
        raise ReleaseProvenanceError("publishable artifacts are not for the expected application release")
    _verify_artifact_contract(artifacts, provenance)
    return provenance


def verify_staged_provenance(
    stage_receipt_path: Path,
    *,
    webapp_fi_source_attestation_public_key: bytes,
) -> tuple[StageReceipt, Provenance]:
    receipt = load_stage_receipt(stage_receipt_path)
    if receipt.source_site != STAGE_SOURCE_SITE or receipt.destination_site != STAGE_DESTINATION_SITE:
        raise ReleaseProvenanceError("artifact stage is not the fixed webapp_fi to webapp_ir transfer")
    provenance = load_provenance(
        receipt.artifacts[PROVENANCE_ARTIFACT].path,
        must_absent=True,
        webapp_fi_source_attestation_public_key=webapp_fi_source_attestation_public_key,
    )
    if receipt.release_sha != provenance.application.release_sha:
        raise ReleaseProvenanceError("artifact stage namespace is not pinned to the application release")
    _verify_artifact_contract(receipt.artifacts, provenance)
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
    webapp_fi_source_role_attestation: Path,
    webapp_fi_image_export_receipt: Path,
    webapp_fi_controller_image_adoption_receipt: Path,
    webapp_fi_source_attestation_public_key_file: Path,
) -> dict[str, Any]:
    """Create only control/provenance artifacts tied to a prepared app receipt."""

    preparation = _preparation_receipt(application_preparation_receipt)
    source_proof = load_webapp_fi_source_proof(
        source_role_attestation=webapp_fi_source_role_attestation,
        image_export_receipt=webapp_fi_image_export_receipt,
        controller_image_adoption_receipt=webapp_fi_controller_image_adoption_receipt,
        public_key=load_webapp_fi_source_attestation_public_key(
            webapp_fi_source_attestation_public_key_file
        ),
    )
    attestation = source_proof.source_role_attestation
    image_export = source_proof.image_export_receipt
    adoption = source_proof.controller_image_adoption_receipt
    if (
        attestation.campaign_id != preparation.campaign_id
        or attestation.release_sha != preparation.release_sha
        or attestation.release_tree != preparation.release_tree
    ):
        raise ReleaseProvenanceError("webapp_fi_source_proof does not bind the prepared application release")
    control_sha, control_tree = _inspect_commit(control_repository, control_release_sha)
    if control_sha == preparation.release_sha:
        raise ReleaseProvenanceError("application and control commits must be distinct")
    selected = [item for item in preparation.images if item["image_id"] == attestation.image_id]
    if len(selected) != 1:
        raise ReleaseProvenanceError("FI source-proof application image is absent from the prepared image manifest")
    app_repo_digest = _source_proof_app_repo_digest(source_proof)
    if app_repo_digest is None:
        if selected[0]["repo_digests"]:
            raise ReleaseProvenanceError("FI source-proof application image has an unbound repo digest")
    elif app_repo_digest not in selected[0]["repo_digests"]:
        raise ReleaseProvenanceError("FI source-proof application image repo digest is absent from the prepared image manifest")
    output = _safe_path(str(output_directory), field="output_directory")
    _require_directory(output.parent, field="output_directory parent", private=True)
    if output.exists() or output.is_symlink():
        raise ReleaseProvenanceError("output_directory must be a new immutable directory")
    runtime = RuntimeImageContract(
        app_image_id=attestation.image_id,
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
        "campaign_id": preparation.campaign_id,
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
        "webapp_fi_source_proof": {
            "source_role_attestation": attestation.payload,
            "image_export_receipt": image_export.payload,
            "controller_image_adoption_receipt": adoption.payload,
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
                "campaign_id": preparation.campaign_id,
                "control_bundle_sha256": control_sha256,
                "control_release_sha": control_sha,
                "image_manifest_sha256": runtime.image_manifest_sha256,
                "webapp_fi_adoption_receipt_sha256": adoption.sha256,
                "webapp_fi_attestation_key_id": attestation.key_id,
                "webapp_fi_attestation_sha256": attestation.sha256,
                "webapp_fi_image_export_sha256": image_export.sha256,
                "webapp_fi_source_archive_sha256": adoption.plaintext_sha256,
                "webapp_fi_source_archive_version_id": adoption.version_id,
                "webapp_fi_source_image_id": adoption.image_id,
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
        "webapp_fi_source_proof": {
            "campaign_id": attestation.campaign_id,
            "key_id": attestation.key_id,
            "source_role_attestation_sha256": attestation.sha256,
            "image_export_receipt_sha256": image_export.sha256,
            "controller_image_adoption_receipt_sha256": adoption.sha256,
            "image_id": attestation.image_id,
            "image_reference": attestation.image_reference,
            "archive_object_key": adoption.object_key,
            "archive_version_id": adoption.version_id,
            "image_archive_sha256": adoption.plaintext_sha256,
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
        "dispatcher": {
            "path": str(dispatcher.path),
            "sha256": dispatcher.sha256,
            "control_release_sha": dispatcher.control_release_sha,
        },
        "runtime_images": runtime_images,
    }


def install_release_roots(
    *,
    stage_receipt_path: Path,
    bootstrap_receipt_path: Path,
    receipt_path: Path,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    bootstrap = load_bootstrap_receive_receipt(bootstrap_receipt_path)
    stage, provenance = verify_staged_provenance(
        stage_receipt_path,
        webapp_fi_source_attestation_public_key=bootstrap.webapp_fi_source_attestation_public_key,
    )
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


def load_installed_release_receipt(path: Path) -> dict[str, Any]:
    value = _read_private_json(path, field="installed release provenance receipt")
    _fields(
        value,
        expected={"schema", "status", "installed_at", "stage", "application", "control", "dispatcher", "runtime_images"},
        field="installed release provenance receipt",
    )
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
    dispatcher = _installed_dispatcher(value.get("dispatcher"), control=control)
    images = _runtime_contract(value.get("runtime_images"))
    runtime_images: dict[str, Any] = {
        "app_image_id": images.app_image_id,
        "image_bundle_sha256": images.image_bundle_sha256,
        "image_manifest_sha256": images.image_manifest_sha256,
    }
    if images.app_repo_digest is not None:
        runtime_images["app_repo_digest"] = images.app_repo_digest
    return {
        "application": application,
        "control": control,
        "dispatcher": dispatcher,
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
    build.add_argument("--webapp-fi-source-role-attestation", type=Path, required=True)
    build.add_argument("--webapp-fi-image-export-receipt", type=Path, required=True)
    build.add_argument("--webapp-fi-controller-image-adoption-receipt", type=Path, required=True)
    build.add_argument("--webapp-fi-source-attestation-public-key-file", type=Path, required=True)
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
                webapp_fi_source_role_attestation=args.webapp_fi_source_role_attestation,
                webapp_fi_image_export_receipt=args.webapp_fi_image_export_receipt,
                webapp_fi_controller_image_adoption_receipt=args.webapp_fi_controller_image_adoption_receipt,
                webapp_fi_source_attestation_public_key_file=args.webapp_fi_source_attestation_public_key_file,
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
