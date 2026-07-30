#!/usr/bin/env python3
"""Install and attest a detached WebApp-FI source-proof helper package.

This installer accepts only a fresh package archive that a separate,
Object-Storage-only receiver has already bound to one immutable VersionId.  It
does not download, upload, delete, SSH, start or stop services, change
``current``, touch application data, create volumes, or execute a data-plane
capture.  It contains no S3 credential, transfer client, or recurring timer.

The package is installed below a new root-only candidate.  The candidate is
not an application release and no ``current`` pointer is changed.  A failed
candidate is intentionally retained without an install receipt; a retry must
use a new candidate and a fresh explicit operation.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
from typing import Any, Mapping, Sequence


PACKAGE_SCHEMA = "gold-trade-webapp-fi-source-adoption-package-v1"
CONTRACT_SCHEMA = "gold-trade-webapp-fi-source-adoption-contract-v1"
PREPARATION_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-adoption-preparation-v1"
DELIVERY_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-adoption-delivery-v1"
INSTALL_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-adoption-install-receipt-v1"
ATTESTATION_SCHEMA = "gold-trade-webapp-fi-source-role-attestation-v2"
IMAGE_EXPORT_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-image-export-receipt-v2"
SOURCE_ROLE_CONFIG_SCHEMA = "gold-trade-webapp-fi-source-role-config-v2"
STATIC_ASSET_PROOF_SCHEMA = "gold-trade-webapp-fi-static-asset-provenance-v1"
DELIVERY_ENVELOPE_SCHEMA = "gold-trade-webapp-fi-source-adoption-delivery-envelope-v1"
SIGNER_ENROLLMENT_CERTIFICATE_SCHEMA = "gold-trade-webapp-fi-source-signer-enrollment-certificate-v2"
SIGNER_ENROLLMENT_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-signer-enrollment-receipt-v2"
SIGNER_ENROLLMENT_CONSUMPTION_SCHEMA = "gold-trade-webapp-fi-source-signer-enrollment-consumption-v2"

ATTESTATION_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-source-role-attestation-v2\x00"
IMAGE_EXPORT_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-source-image-export-v2\x00"
STATIC_ASSET_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-static-asset-provenance-v1\x00"
DELIVERY_ENVELOPE_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-source-adoption-delivery-envelope-v1\x00"
SIGNER_ENROLLMENT_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-source-signer-enrollment-v2\x00"

PACKAGE_ARCHIVE_NAME = "webapp-fi-source-adoption.tar"
PACKAGE_MANIFEST_MEMBER = "source-adoption-package.json"
PREPARATION_RECEIPT_NAME = "source-adoption-preparation-receipt.json"
INSTALL_RECEIPT_NAME = "source-adoption-install-receipt.json"
CANONICAL_RELEASE_TREE_MEMBER = "config/canonical-release-tree.json"
CONTRACT_MEMBER = "config/source-adoption-contract.json"

PACKAGE_SOURCE_SITE = "bot_fi"
PACKAGE_DESTINATION_SITE = "webapp_fi"
SNAPSHOT_DESTINATION_SITE = "webapp_ir"

MAX_ARCHIVE_BYTES = 24 * 1024 * 1024
MAX_PACKAGE_MEMBER_BYTES = 8 * 1024 * 1024
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_CANONICAL_RELEASE_FILES = 100_000
MAX_IMAGE_EXPORT_BYTES = 100 * 1024 * 1024 * 1024
MAX_ENROLLMENT_CERTIFICATE_LIFETIME_SECONDS = 60 * 60
MAX_OBSERVATION_AGE_SECONDS = 15 * 60

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
ALEMBIC_RE = re.compile(r"^[0-9a-f]{12}$")
PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ATTESTATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,511}$")
OBJECT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/=-]{2,1023}$")
CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{20,128}$")
UTC_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

RUNTIME_CODE_PROJECTION_RELATIVES = (
    "api",
    "bot",
    "core",
    "src",
    "models",
    "migrations",
    "scripts",
    "main.py",
    "schemas.py",
    "trading_settings.json",
)
RUNTIME_STATIC_ASSET_RELATIVE = "mini_app_dist"
RUNTIME_DATA_MOUNT_TARGETS = frozenset({"/app/uploads", "/app/audit_trail"})

SOURCE_PAYLOAD_FILES = (
    "scripts/install_webapp_fi_source_adoption.py",
    "deploy/production/webapp-fi-source-role.json.example",
)
PACKAGE_PAYLOAD_FILES = (*SOURCE_PAYLOAD_FILES, CONTRACT_MEMBER, CANONICAL_RELEASE_TREE_MEMBER)
PACKAGE_FILES = (*PACKAGE_PAYLOAD_FILES, PACKAGE_MANIFEST_MEMBER)


class SourceAdoptionInstallError(RuntimeError):
    """A source-adoption install or read-only attestation is unsafe."""


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise SourceAdoptionInstallError("WebApp-FI source-adoption operations must run as root")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SourceAdoptionInstallError("JSON input contains duplicate keys")
        value[key] = item
    return value


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_absolute(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise SourceAdoptionInstallError(f"{field} must be an absolute path")
    return path


def _require_safe_ancestors(path: Path, *, field: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            state = current.lstat()
        except OSError as exc:
            raise SourceAdoptionInstallError(f"{field} ancestor does not exist") from exc
        if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
            raise SourceAdoptionInstallError(f"{field} has an unsafe ancestor")
        if state.st_uid != 0 or stat.S_IMODE(state.st_mode) & 0o022:
            raise SourceAdoptionInstallError(f"{field} ancestor is not root-controlled")


def require_root_only_directory(path: Path, *, field: str) -> Path:
    path = _require_absolute(path, field=field)
    _require_safe_ancestors(path.parent, field=field)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SourceAdoptionInstallError(f"cannot inspect {field}") from exc
    if resolved != path or stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        raise SourceAdoptionInstallError(f"{field} must be one canonical non-symlink directory")
    if state.st_uid != 0 or stat.S_IMODE(state.st_mode) & 0o077:
        raise SourceAdoptionInstallError(f"{field} must be root-only")
    return resolved


def require_root_only_file(path: Path, *, field: str, maximum_bytes: int = MAX_RECEIPT_BYTES) -> Path:
    path = _require_absolute(path, field=field)
    _require_safe_ancestors(path.parent, field=field)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SourceAdoptionInstallError(f"cannot inspect {field}") from exc
    if resolved != path or stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode):
        raise SourceAdoptionInstallError(f"{field} must be one canonical regular file")
    if state.st_uid != 0 or stat.S_IMODE(state.st_mode) & 0o077 or not 1 <= state.st_size <= maximum_bytes:
        raise SourceAdoptionInstallError(f"{field} has unsafe ownership, mode, or size")
    return resolved


def _parse_canonical_json(payload: bytes, *, field: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceAdoptionInstallError(f"{field} is not valid strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise SourceAdoptionInstallError(f"{field} must be a JSON object")
    if payload != canonical_json_bytes(value) + b"\n":
        raise SourceAdoptionInstallError(f"{field} must use canonical JSON")
    return value


def _read_private_json(path: Path, *, field: str, maximum_bytes: int = MAX_RECEIPT_BYTES) -> tuple[dict[str, Any], bytes]:
    path = require_root_only_file(path, field=field, maximum_bytes=maximum_bytes)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise SourceAdoptionInstallError(f"cannot read {field}") from exc
    return _parse_canonical_json(payload, field=field), payload


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise SourceAdoptionInstallError(f"{field} is invalid")
    return value


def _require_size(value: object, *, field: str, maximum: int, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise SourceAdoptionInstallError(f"{field} is invalid")
    return value


def _require_package_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not PACKAGE_ID_RE.fullmatch(value):
        raise SourceAdoptionInstallError(f"{field} is invalid")
    return value


def _require_application(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"release_sha", "expected_alembic_revision"}:
        raise SourceAdoptionInstallError(f"{field} is invalid")
    release = value.get("release_sha")
    revision = value.get("expected_alembic_revision")
    if not isinstance(release, str) or not RELEASE_RE.fullmatch(release):
        raise SourceAdoptionInstallError(f"{field}.release_sha is invalid")
    if not isinstance(revision, str) or not ALEMBIC_RE.fullmatch(revision):
        raise SourceAdoptionInstallError(f"{field}.expected_alembic_revision is invalid")
    return {"release_sha": release, "expected_alembic_revision": revision}


def _require_tooling(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"control_commit", "control_tree"}:
        raise SourceAdoptionInstallError(f"{field} is invalid")
    commit = value.get("control_commit")
    tree = value.get("control_tree")
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise SourceAdoptionInstallError(f"{field}.control_commit is invalid")
    if not isinstance(tree, str) or not COMMIT_RE.fullmatch(tree):
        raise SourceAdoptionInstallError(f"{field}.control_tree is invalid")
    return {"control_commit": commit, "control_tree": tree}


def _require_hashes(value: object, *, field: str, names: Sequence[str]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(names):
        raise SourceAdoptionInstallError(f"{field} does not match the package contract")
    return {name: _require_sha256(value.get(name), field=f"{field}.{name}") for name in names}


def _validate_canonical_release_tree_descriptor(payload: bytes) -> dict[str, Any]:
    descriptor = _parse_canonical_json(payload, field="canonical application release descriptor")
    expected = {"schema", "status", "application", "files", "files_sha256"}
    if (
        set(descriptor) != expected
        or descriptor.get("schema") != "gold-trade-webapp-fi-canonical-release-tree-v1"
        or descriptor.get("status") != "prepared"
    ):
        raise SourceAdoptionInstallError("canonical application release descriptor is unsupported")
    application_raw = descriptor.get("application")
    if not isinstance(application_raw, Mapping) or set(application_raw) != {"release_sha", "git_tree"}:
        raise SourceAdoptionInstallError("canonical application release descriptor application is invalid")
    release = application_raw.get("release_sha")
    tree = application_raw.get("git_tree")
    if not isinstance(release, str) or not RELEASE_RE.fullmatch(release):
        raise SourceAdoptionInstallError("canonical application release descriptor release is invalid")
    if not isinstance(tree, str) or not COMMIT_RE.fullmatch(tree):
        raise SourceAdoptionInstallError("canonical application release descriptor tree is invalid")
    raw_files = descriptor.get("files")
    if not isinstance(raw_files, list) or not raw_files or len(raw_files) > MAX_CANONICAL_RELEASE_FILES:
        raise SourceAdoptionInstallError("canonical application release descriptor files are invalid")
    files: list[dict[str, Any]] = []
    prior = ""
    for item in raw_files:
        if not isinstance(item, Mapping) or set(item) != {"path", "mode", "sha256", "bytes"}:
            raise SourceAdoptionInstallError("canonical application release descriptor file is invalid")
        path = item.get("path")
        if not isinstance(path, str):
            raise SourceAdoptionInstallError("canonical application release descriptor path is invalid")
        pure = PurePosixPath(path)
        if (
            pure.as_posix() != path
            or not path
            or path.startswith("/")
            or ".." in pure.parts
            or any(not part or any(ord(character) < 0x20 for character in part) for part in pure.parts)
        ):
            raise SourceAdoptionInstallError("canonical application release descriptor path is unsafe")
        if prior and path <= prior:
            raise SourceAdoptionInstallError("canonical application release descriptor paths are not strictly ordered")
        prior = path
        mode = item.get("mode")
        if mode not in {"100644", "100755"}:
            raise SourceAdoptionInstallError("canonical application release descriptor mode is invalid")
        digest = _require_sha256(item.get("sha256"), field="canonical application release descriptor sha256")
        size = _require_size(
            item.get("bytes"),
            field="canonical application release descriptor bytes",
            maximum=MAX_IMAGE_EXPORT_BYTES,
            minimum=0,
        )
        files.append({"path": path, "mode": mode, "sha256": digest, "bytes": size})
    if descriptor.get("files_sha256") != sha256_bytes(canonical_json_bytes(files)):
        raise SourceAdoptionInstallError("canonical application release descriptor file hash is invalid")
    return {"application": {"release_sha": release, "git_tree": tree}, "files": files}


def _validate_contract(payload: bytes) -> dict[str, Any]:
    contract = _parse_canonical_json(payload, field="source-adoption contract")
    expected = {
        "schema",
        "status",
        "source_site",
        "destination_site",
        "application",
        "tooling",
        "canonical_release_tree_sha256",
        "external_material",
        "snapshot_transport",
        "forbidden_actions",
    }
    if set(contract) != expected or contract.get("schema") != CONTRACT_SCHEMA or contract.get("status") != "prepared":
        raise SourceAdoptionInstallError("source-adoption contract is unsupported")
    if contract.get("source_site") != PACKAGE_DESTINATION_SITE or contract.get("destination_site") != SNAPSHOT_DESTINATION_SITE:
        raise SourceAdoptionInstallError("source-adoption contract has an invalid site binding")
    application = _require_application(contract.get("application"), field="source-adoption contract application")
    tooling = _require_tooling(contract.get("tooling"), field="source-adoption contract tooling")
    descriptor_sha = _require_sha256(
        contract.get("canonical_release_tree_sha256"), field="source-adoption contract canonical_release_tree_sha256"
    )
    if contract.get("external_material") != [
        "fresh_webapp_fi_bootstrap_age_identity",
        "separately_authorized_webapp_fi_source_signing_key_enrollment",
        "wa_ir_public_age_recipient",
        "ephemeral_version_bound_object_storage_control",
    ]:
        raise SourceAdoptionInstallError("source-adoption contract external material is unsupported")
    if contract.get("snapshot_transport") != {
        "payload_path": "private_versioned_object_storage_age_only",
        "one_off_publication_only": True,
        "direct_webapp_fi_to_webapp_ir_transfer": False,
        "automatic_deletion": False,
    }:
        raise SourceAdoptionInstallError("source-adoption contract transport is unsupported")
    if contract.get("forbidden_actions") != [
        "current",
        "service",
        "container_lifecycle",
        "volume",
        "application_data",
        "migration",
        "seed_restore",
        "failover",
        "full_matrix",
    ]:
        raise SourceAdoptionInstallError("source-adoption contract forbidden actions are unsupported")
    return {"application": application, "tooling": tooling, "canonical_release_tree_sha256": descriptor_sha}


def _validate_package_manifest(payload: bytes) -> dict[str, Any]:
    manifest = _parse_canonical_json(payload, field="source-adoption package manifest")
    expected = {
        "schema",
        "status",
        "package_id",
        "source_site",
        "destination_site",
        "application",
        "tooling",
        "files",
        "contract_sha256",
    }
    if set(manifest) != expected or manifest.get("schema") != PACKAGE_SCHEMA or manifest.get("status") != "prepared":
        raise SourceAdoptionInstallError("source-adoption package manifest is unsupported")
    if manifest.get("source_site") != PACKAGE_SOURCE_SITE or manifest.get("destination_site") != PACKAGE_DESTINATION_SITE:
        raise SourceAdoptionInstallError("source-adoption package manifest site binding is invalid")
    return {
        "package_id": _require_package_id(manifest.get("package_id"), field="source-adoption package manifest package_id"),
        "application": _require_application(manifest.get("application"), field="source-adoption package manifest application"),
        "tooling": _require_tooling(manifest.get("tooling"), field="source-adoption package manifest tooling"),
        "files": _require_hashes(manifest.get("files"), field="source-adoption package manifest files", names=PACKAGE_PAYLOAD_FILES),
        "contract_sha256": _require_sha256(manifest.get("contract_sha256"), field="source-adoption package manifest contract_sha256"),
    }


def _validate_preparation_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema",
        "status",
        "package_id",
        "package_directory",
        "source_site",
        "destination_site",
        "application",
        "tooling",
        "archive",
        "package_manifest",
        "receipt_sha256",
    }
    if set(value) != expected or value.get("schema") != PREPARATION_RECEIPT_SCHEMA or value.get("status") != "prepared":
        raise SourceAdoptionInstallError("source-adoption preparation receipt is unsupported")
    receipt_sha = _require_sha256(value.get("receipt_sha256"), field="source-adoption preparation receipt receipt_sha256")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if sha256_bytes(canonical_json_bytes(unsigned)) != receipt_sha:
        raise SourceAdoptionInstallError("source-adoption preparation receipt hash is invalid")
    if value.get("source_site") != PACKAGE_SOURCE_SITE or value.get("destination_site") != PACKAGE_DESTINATION_SITE:
        raise SourceAdoptionInstallError("source-adoption preparation receipt site binding is invalid")
    archive = value.get("archive")
    if not isinstance(archive, Mapping) or set(archive) != {"name", "sha256", "bytes"} or archive.get("name") != PACKAGE_ARCHIVE_NAME:
        raise SourceAdoptionInstallError("source-adoption preparation receipt archive is invalid")
    manifest = value.get("package_manifest")
    if not isinstance(manifest, Mapping) or set(manifest) != {"name", "sha256"} or manifest.get("name") != PACKAGE_MANIFEST_MEMBER:
        raise SourceAdoptionInstallError("source-adoption preparation receipt package manifest is invalid")
    package_directory = value.get("package_directory")
    if not isinstance(package_directory, str) or not package_directory.startswith("/"):
        raise SourceAdoptionInstallError("source-adoption preparation receipt package directory is invalid")
    return {
        "package_id": _require_package_id(value.get("package_id"), field="source-adoption preparation receipt package_id"),
        "application": _require_application(value.get("application"), field="source-adoption preparation receipt application"),
        "tooling": _require_tooling(value.get("tooling"), field="source-adoption preparation receipt tooling"),
        "archive_sha256": _require_sha256(archive.get("sha256"), field="source-adoption preparation receipt archive sha256"),
        "archive_bytes": _require_size(archive.get("bytes"), field="source-adoption preparation receipt archive bytes", maximum=MAX_ARCHIVE_BYTES),
        "package_manifest_sha256": _require_sha256(manifest.get("sha256"), field="source-adoption preparation receipt package manifest sha256"),
    }


def _validate_delivery_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the URL-free receipt from the generic FI Object Storage receiver."""

    expected = {
        "schema",
        "status",
        "source_site",
        "destination_site",
        "control_commit",
        "package_id",
        "object",
        "archive",
        "receipt_sha256",
    }
    if set(value) != expected or value.get("schema") != DELIVERY_RECEIPT_SCHEMA or value.get("status") != "received":
        raise SourceAdoptionInstallError("source-adoption delivery receipt is unsupported")
    receipt_sha = _require_sha256(value.get("receipt_sha256"), field="source-adoption delivery receipt receipt_sha256")
    if sha256_bytes(canonical_json_bytes({key: item for key, item in value.items() if key != "receipt_sha256"})) != receipt_sha:
        raise SourceAdoptionInstallError("source-adoption delivery receipt hash is invalid")
    if value.get("source_site") != PACKAGE_SOURCE_SITE or value.get("destination_site") != PACKAGE_DESTINATION_SITE:
        raise SourceAdoptionInstallError("source-adoption delivery receipt site binding is invalid")
    commit = value.get("control_commit")
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise SourceAdoptionInstallError("source-adoption delivery receipt control_commit is invalid")
    package_id = _require_package_id(value.get("package_id"), field="source-adoption delivery receipt package_id")
    object_value = value.get("object")
    object_expected = {"object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes"}
    if not isinstance(object_value, Mapping) or set(object_value) != object_expected:
        raise SourceAdoptionInstallError("source-adoption delivery receipt object is invalid")
    object_key = object_value.get("object_key")
    version_id = object_value.get("version_id")
    if not isinstance(object_key, str) or not OBJECT_KEY_RE.fullmatch(object_key):
        raise SourceAdoptionInstallError("source-adoption delivery receipt object key is invalid")
    if not isinstance(version_id, str) or not version_id or len(version_id) > 1024 or any(ord(item) < 0x20 for item in version_id):
        raise SourceAdoptionInstallError("source-adoption delivery receipt version_id is invalid")
    archive = value.get("archive")
    if not isinstance(archive, Mapping) or set(archive) != {"sha256", "bytes"}:
        raise SourceAdoptionInstallError("source-adoption delivery receipt archive is invalid")
    return {
        "control_commit": commit,
        "package_id": package_id,
        "object": {
            "object_key": object_key,
            "version_id": version_id,
            "ciphertext_sha256": _require_sha256(object_value.get("ciphertext_sha256"), field="source-adoption delivery receipt ciphertext_sha256"),
            "ciphertext_bytes": _require_size(object_value.get("ciphertext_bytes"), field="source-adoption delivery receipt ciphertext_bytes", maximum=MAX_ARCHIVE_BYTES + 1024 * 1024),
            "plaintext_sha256": _require_sha256(object_value.get("plaintext_sha256"), field="source-adoption delivery receipt plaintext_sha256"),
            "plaintext_bytes": _require_size(object_value.get("plaintext_bytes"), field="source-adoption delivery receipt plaintext_bytes", maximum=MAX_ARCHIVE_BYTES),
        },
        "archive_sha256": _require_sha256(archive.get("sha256"), field="source-adoption delivery receipt archive sha256"),
        "archive_bytes": _require_size(archive.get("bytes"), field="source-adoption delivery receipt archive bytes", maximum=MAX_ARCHIVE_BYTES),
    }


def _read_archive_members(path: Path) -> dict[str, bytes]:
    path = require_root_only_file(path, field="source-adoption archive", maximum_bytes=MAX_ARCHIVE_BYTES)
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(path, mode="r:") as archive:
            entries = archive.getmembers()
            if len(entries) != len(PACKAGE_FILES) or {entry.name for entry in entries} != set(PACKAGE_FILES):
                raise SourceAdoptionInstallError("source-adoption archive members do not match the package contract")
            for entry in entries:
                pure = PurePosixPath(entry.name)
                if (
                    pure.as_posix() != entry.name
                    or entry.name.startswith("/")
                    or ".." in pure.parts
                    or not entry.isfile()
                    or entry.issym()
                    or entry.islnk()
                    or not 1 <= entry.size <= MAX_PACKAGE_MEMBER_BYTES
                ):
                    raise SourceAdoptionInstallError("source-adoption archive contains an unsafe member")
                handle = archive.extractfile(entry)
                if handle is None:
                    raise SourceAdoptionInstallError("source-adoption archive member cannot be read")
                content = handle.read(entry.size + 1)
                if len(content) != entry.size:
                    raise SourceAdoptionInstallError("source-adoption archive member size changed")
                members[entry.name] = content
    except (OSError, tarfile.TarError) as exc:
        raise SourceAdoptionInstallError("source-adoption archive cannot be verified") from exc
    return members


def verify_package_inputs(
    *,
    archive: Path,
    preparation_receipt: Path,
    delivery_receipt: Path,
    delivery_envelope: Path,
    pinned_controller_public_key_base64: str,
    expected_campaign_id: str,
    expected_fi_bootstrap_recipient: str,
    expected_control_commit: str,
    expected_application_release_sha: str,
) -> dict[str, Any]:
    _require_root_execution()
    if not COMMIT_RE.fullmatch(expected_control_commit):
        raise SourceAdoptionInstallError("expected_control_commit is invalid")
    if not RELEASE_RE.fullmatch(expected_application_release_sha):
        raise SourceAdoptionInstallError("expected_application_release_sha is invalid")
    expected_campaign_id = _require_campaign_id(expected_campaign_id, field="expected_campaign_id")
    if not AGE_RECIPIENT_RE.fullmatch(expected_fi_bootstrap_recipient):
        raise SourceAdoptionInstallError("expected_fi_bootstrap_recipient is invalid")
    archive = require_root_only_file(archive, field="source-adoption archive", maximum_bytes=MAX_ARCHIVE_BYTES)
    preparation_value, preparation_raw = _read_private_json(preparation_receipt, field="source-adoption preparation receipt")
    preparation = _validate_preparation_receipt(preparation_value)
    delivery_value, delivery_raw = _read_private_json(delivery_receipt, field="source-adoption delivery receipt")
    delivery = _validate_delivery_receipt(delivery_value)
    actual_archive_sha, actual_archive_bytes = sha256_file(archive)
    if (
        actual_archive_sha != preparation["archive_sha256"]
        or actual_archive_bytes != preparation["archive_bytes"]
        or actual_archive_sha != delivery["archive_sha256"]
        or actual_archive_bytes != delivery["archive_bytes"]
        or actual_archive_sha != delivery["object"]["plaintext_sha256"]
        or actual_archive_bytes != delivery["object"]["plaintext_bytes"]
    ):
        raise SourceAdoptionInstallError("source-adoption archive is not bound to both immutable receipts")
    if preparation["package_id"] != delivery["package_id"]:
        raise SourceAdoptionInstallError("source-adoption receipt package IDs differ")
    if preparation["tooling"]["control_commit"] != expected_control_commit or delivery["control_commit"] != expected_control_commit:
        raise SourceAdoptionInstallError("source-adoption control commit is unexpected")
    if preparation["application"]["release_sha"] != expected_application_release_sha:
        raise SourceAdoptionInstallError("source-adoption application release is unexpected")
    members = _read_archive_members(archive)
    manifest_raw = members[PACKAGE_MANIFEST_MEMBER]
    manifest = _validate_package_manifest(manifest_raw)
    if sha256_bytes(manifest_raw) != preparation["package_manifest_sha256"]:
        raise SourceAdoptionInstallError("source-adoption package manifest does not match preparation receipt")
    hashes = {name: sha256_bytes(members[name]) for name in PACKAGE_PAYLOAD_FILES}
    if hashes != manifest["files"]:
        raise SourceAdoptionInstallError("source-adoption package payload hashes do not match manifest")
    contract_raw = members[CONTRACT_MEMBER]
    contract = _validate_contract(contract_raw)
    descriptor_raw = members[CANONICAL_RELEASE_TREE_MEMBER]
    descriptor = _validate_canonical_release_tree_descriptor(descriptor_raw)
    if sha256_bytes(contract_raw) != manifest["contract_sha256"] or sha256_bytes(descriptor_raw) != contract["canonical_release_tree_sha256"]:
        raise SourceAdoptionInstallError("source-adoption descriptor or contract binding is invalid")
    if (
        manifest["package_id"] != preparation["package_id"]
        or manifest["application"] != preparation["application"]
        or manifest["tooling"] != preparation["tooling"]
        or contract["application"] != preparation["application"]
        or contract["tooling"] != preparation["tooling"]
        or descriptor["application"]["release_sha"] != preparation["application"]["release_sha"]
    ):
        raise SourceAdoptionInstallError("source-adoption package bindings are inconsistent")
    envelope = _validate_signed_delivery_envelope(
        envelope=delivery_envelope,
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        expected_control_commit=expected_control_commit,
        expected_application=preparation["application"],
        expected_descriptor_sha256=sha256_bytes(descriptor_raw),
    )
    if envelope["campaign_id"] != expected_campaign_id or envelope["recipient"] != expected_fi_bootstrap_recipient:
        raise SourceAdoptionInstallError("controller-signed source-adoption delivery envelope target is unexpected")
    if envelope["package_id"] != preparation["package_id"] or envelope["tooling"] != preparation["tooling"]:
        raise SourceAdoptionInstallError("controller-signed source-adoption delivery envelope package binding is inconsistent")
    if envelope["object"] != delivery["object"]:
        raise SourceAdoptionInstallError("controller-signed source-adoption delivery envelope immutable object differs from receipt")
    return {
        "archive": archive,
        "archive_sha256": actual_archive_sha,
        "archive_bytes": actual_archive_bytes,
        "members": members,
        "package_id": preparation["package_id"],
        "application": preparation["application"],
        "tooling": preparation["tooling"],
        "descriptor_sha256": sha256_bytes(descriptor_raw),
        "preparation_receipt_sha256": sha256_bytes(preparation_raw),
        "delivery_receipt_sha256": sha256_bytes(delivery_raw),
        "delivery_object": delivery["object"],
        "campaign_id": envelope["campaign_id"],
        "delivery_envelope_sha256": envelope["sha256"],
        "controller_public_key_base64": envelope["controller_public_key_base64"],
        "fi_bootstrap_recipient": envelope["recipient"],
    }


def _create_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except OSError as exc:
        raise SourceAdoptionInstallError("cannot create a new root-only source-adoption directory") from exc
    state = path.lstat()
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode) or state.st_uid != 0:
        raise SourceAdoptionInstallError("new source-adoption directory is unsafe")
    os.chmod(path, 0o700)
    if stat.S_IMODE(path.lstat().st_mode) != 0o700:
        raise SourceAdoptionInstallError("new source-adoption directory mode is unsafe")


def _write_new_member(candidate: Path, relative: str, content: bytes) -> Path:
    pure = PurePosixPath(relative)
    if pure.as_posix() != relative or relative.startswith("/") or ".." in pure.parts:
        raise SourceAdoptionInstallError("source-adoption member path is unsafe")
    parent = candidate
    for part in pure.parts[:-1]:
        parent = parent / part
        if not parent.exists():
            _create_directory(parent)
        state = parent.lstat()
        if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode) or state.st_uid != 0 or stat.S_IMODE(state.st_mode) != 0o700:
            raise SourceAdoptionInstallError("source-adoption member parent is unsafe")
    target = candidate / relative
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags, 0o600)
    except OSError as exc:
        raise SourceAdoptionInstallError("cannot create source-adoption member") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        raise
    state = target.lstat()
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode) or state.st_uid != 0 or stat.S_IMODE(state.st_mode) != 0o600:
        raise SourceAdoptionInstallError("new source-adoption member is unsafe")
    return target


def _write_new_private_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = canonical_json_bytes(value) + b"\n"
    if b"https://" in encoded or b"presigned" in encoded.lower() or b"\"url\"" in encoded.lower():
        raise SourceAdoptionInstallError("receipt must not persist a URL")
    _write_new_member(path.parent, path.name, encoded)


def install_source_adoption(
    *,
    archive: Path,
    preparation_receipt: Path,
    delivery_receipt: Path,
    delivery_envelope: Path,
    pinned_controller_public_key_base64: str,
    expected_campaign_id: str,
    expected_fi_bootstrap_recipient: str,
    staging_root: Path,
    expected_control_commit: str,
    expected_application_release_sha: str,
    apply: bool,
) -> dict[str, Any]:
    _require_root_execution()
    verified = verify_package_inputs(
        archive=archive,
        preparation_receipt=preparation_receipt,
        delivery_receipt=delivery_receipt,
        delivery_envelope=delivery_envelope,
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        expected_campaign_id=expected_campaign_id,
        expected_fi_bootstrap_recipient=expected_fi_bootstrap_recipient,
        expected_control_commit=expected_control_commit,
        expected_application_release_sha=expected_application_release_sha,
    )
    root = require_root_only_directory(staging_root, field="source-adoption staging root")
    candidate = root / f"installed-{verified['tooling']['control_commit']}-{verified['package_id']}"
    if candidate.exists() or candidate.is_symlink():
        raise SourceAdoptionInstallError("source-adoption candidate already exists")
    plan = {
        "schema": INSTALL_RECEIPT_SCHEMA,
        "status": "planned" if not apply else "installing",
        "candidate_directory": str(candidate),
        "campaign_id": verified["campaign_id"],
        "package_id": verified["package_id"],
        "application": verified["application"],
        "tooling": verified["tooling"],
        "archive_sha256": verified["archive_sha256"],
        "object_storage_delivery": "private_versioned_age_only",
        "direct_webapp_fi_to_webapp_ir_transfer": False,
        "current_changed": False,
        "service_changed": False,
        "container_changed": False,
        "volume_changed": False,
        "application_data_changed": False,
        "automatic_deletion": False,
        "controller_signed_delivery_envelope_required": True,
    }
    if not apply:
        return plan
    _create_directory(candidate)
    for relative in PACKAGE_FILES:
        _write_new_member(candidate, relative, verified["members"][relative])
    files = {relative: sha256_bytes(verified["members"][relative]) for relative in PACKAGE_PAYLOAD_FILES}
    receipt: dict[str, Any] = {
        "schema": INSTALL_RECEIPT_SCHEMA,
        "status": "installed",
        "installed_at": utc_now(),
        "candidate_directory": str(candidate),
        "source_site": PACKAGE_SOURCE_SITE,
        "destination_site": PACKAGE_DESTINATION_SITE,
        "campaign_id": verified["campaign_id"],
        "package_id": verified["package_id"],
        "application": verified["application"],
        "tooling": verified["tooling"],
        "files": files,
        "canonical_release_tree_sha256": verified["descriptor_sha256"],
        "package": {
            "archive_sha256": verified["archive_sha256"],
            "archive_bytes": verified["archive_bytes"],
            "preparation_receipt_sha256": verified["preparation_receipt_sha256"],
            "delivery_receipt_sha256": verified["delivery_receipt_sha256"],
            "delivery_envelope_sha256": verified["delivery_envelope_sha256"],
            "controller_public_key_base64": verified["controller_public_key_base64"],
            "fi_bootstrap_recipient": verified["fi_bootstrap_recipient"],
            "object_key": verified["delivery_object"]["object_key"],
            "version_id": verified["delivery_object"]["version_id"],
            "ciphertext_sha256": verified["delivery_object"]["ciphertext_sha256"],
            "ciphertext_bytes": verified["delivery_object"]["ciphertext_bytes"],
        },
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    _write_new_private_json(candidate / INSTALL_RECEIPT_NAME, receipt)
    return verify_installed_source_adoption(candidate / INSTALL_RECEIPT_NAME)


def _validate_candidate_layout(candidate: Path) -> None:
    """Reject every unreviewed file below an installed helper candidate."""

    expected_files = set(PACKAGE_FILES) | {INSTALL_RECEIPT_NAME}
    allowed_directories: set[str] = set()
    for item in expected_files:
        parent = PurePosixPath(item).parent
        while parent.as_posix() != ".":
            allowed_directories.add(parent.as_posix())
            parent = parent.parent
    allowed_directories.update({"attestations", "enrollments"})
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    for root_text, directories, filenames in os.walk(candidate, topdown=True, followlinks=False):
        root = Path(root_text)
        for directory in directories:
            path = root / directory
            relative = path.relative_to(candidate).as_posix()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
                raise SourceAdoptionInstallError("source-adoption candidate contains an unsafe directory")
            observed_directories.add(relative)
        for filename in filenames:
            path = root / filename
            relative = path.relative_to(candidate).as_posix()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600:
                raise SourceAdoptionInstallError("source-adoption candidate contains an unsafe file")
            if relative.startswith("attestations/"):
                if PurePosixPath(relative).parent.as_posix() != "attestations" or not ATTESTATION_ID_RE.fullmatch(PurePosixPath(relative).stem):
                    raise SourceAdoptionInstallError("source-adoption candidate contains an unexpected attestation")
            elif relative.startswith("enrollments/"):
                if PurePosixPath(relative).parent.as_posix() != "enrollments" or not CAMPAIGN_ID_RE.fullmatch(PurePosixPath(relative).stem):
                    raise SourceAdoptionInstallError("source-adoption candidate contains an unexpected signer enrollment")
            elif relative not in expected_files:
                raise SourceAdoptionInstallError("source-adoption candidate contains an unexpected file")
            observed_files.add(relative)
    if not expected_files.issubset(observed_files) or not observed_directories.issubset(allowed_directories):
        raise SourceAdoptionInstallError("source-adoption candidate layout is incomplete or unexpected")


def verify_installed_source_adoption(receipt_path: Path) -> dict[str, Any]:
    value, raw = _read_private_json(receipt_path, field="source-adoption install receipt")
    expected = {
        "schema", "status", "installed_at", "candidate_directory", "source_site", "destination_site", "campaign_id", "package_id",
        "application", "tooling", "files", "canonical_release_tree_sha256", "package", "receipt_sha256",
    }
    if set(value) != expected or value.get("schema") != INSTALL_RECEIPT_SCHEMA or value.get("status") != "installed":
        raise SourceAdoptionInstallError("source-adoption install receipt is unsupported")
    receipt_sha = _require_sha256(value.get("receipt_sha256"), field="source-adoption install receipt receipt_sha256")
    if sha256_bytes(canonical_json_bytes({key: item for key, item in value.items() if key != "receipt_sha256"})) != receipt_sha:
        raise SourceAdoptionInstallError("source-adoption install receipt hash is invalid")
    installed_at = value.get("installed_at")
    if not isinstance(installed_at, str) or not installed_at.endswith("Z"):
        raise SourceAdoptionInstallError("source-adoption install receipt timestamp is invalid")
    try:
        if dt.datetime.fromisoformat(installed_at.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise SourceAdoptionInstallError("source-adoption install receipt timestamp is invalid") from exc
    candidate_text = value.get("candidate_directory")
    if not isinstance(candidate_text, str):
        raise SourceAdoptionInstallError("source-adoption install receipt candidate is invalid")
    candidate = require_root_only_directory(Path(candidate_text), field="source-adoption installed candidate")
    if receipt_path != candidate / INSTALL_RECEIPT_NAME:
        raise SourceAdoptionInstallError("source-adoption install receipt is not candidate-bound")
    package_id = _require_package_id(value.get("package_id"), field="source-adoption install receipt package_id")
    campaign_id = _require_campaign_id(value.get("campaign_id"), field="source-adoption install receipt campaign_id")
    application = _require_application(value.get("application"), field="source-adoption install receipt application")
    tooling = _require_tooling(value.get("tooling"), field="source-adoption install receipt tooling")
    if candidate.name != f"installed-{tooling['control_commit']}-{package_id}":
        raise SourceAdoptionInstallError("source-adoption candidate name is not receipt-bound")
    files = _require_hashes(value.get("files"), field="source-adoption install receipt files", names=PACKAGE_PAYLOAD_FILES)
    descriptor_sha = _require_sha256(value.get("canonical_release_tree_sha256"), field="source-adoption install receipt descriptor sha256")
    package = value.get("package")
    package_expected = {
        "archive_sha256", "archive_bytes", "preparation_receipt_sha256", "delivery_receipt_sha256", "delivery_envelope_sha256",
        "controller_public_key_base64", "fi_bootstrap_recipient", "object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes",
    }
    if not isinstance(package, Mapping) or set(package) != package_expected:
        raise SourceAdoptionInstallError("source-adoption install receipt package is invalid")
    _require_sha256(package.get("archive_sha256"), field="source-adoption install receipt archive sha256")
    _require_size(package.get("archive_bytes"), field="source-adoption install receipt archive bytes", maximum=MAX_ARCHIVE_BYTES)
    _require_sha256(package.get("preparation_receipt_sha256"), field="source-adoption install receipt preparation receipt sha256")
    _require_sha256(package.get("delivery_receipt_sha256"), field="source-adoption install receipt delivery receipt sha256")
    _require_sha256(package.get("delivery_envelope_sha256"), field="source-adoption install receipt delivery envelope sha256")
    if not isinstance(package.get("controller_public_key_base64"), str):
        raise SourceAdoptionInstallError("source-adoption install receipt controller public key is invalid")
    _decode_pinned_public_key(package["controller_public_key_base64"])
    if not isinstance(package.get("fi_bootstrap_recipient"), str) or not AGE_RECIPIENT_RE.fullmatch(package["fi_bootstrap_recipient"]):
        raise SourceAdoptionInstallError("source-adoption install receipt bootstrap recipient is invalid")
    object_key = package.get("object_key")
    if not isinstance(object_key, str) or not OBJECT_KEY_RE.fullmatch(object_key):
        raise SourceAdoptionInstallError("source-adoption install receipt object key is invalid")
    version_id = package.get("version_id")
    if not isinstance(version_id, str) or not version_id or len(version_id) > 1024:
        raise SourceAdoptionInstallError("source-adoption install receipt version_id is invalid")
    _require_sha256(package.get("ciphertext_sha256"), field="source-adoption install receipt ciphertext sha256")
    _require_size(package.get("ciphertext_bytes"), field="source-adoption install receipt ciphertext bytes", maximum=MAX_ARCHIVE_BYTES + 1024 * 1024)
    _validate_candidate_layout(candidate)
    for relative, expected_sha in files.items():
        item = candidate / relative
        require_root_only_file(item, field="source-adoption installed member", maximum_bytes=MAX_PACKAGE_MEMBER_BYTES)
        actual_sha, _ = sha256_file(item)
        if actual_sha != expected_sha:
            raise SourceAdoptionInstallError("source-adoption installed member hash changed")
    descriptor = candidate / CANONICAL_RELEASE_TREE_MEMBER
    if sha256_file(descriptor)[0] != descriptor_sha:
        raise SourceAdoptionInstallError("source-adoption canonical descriptor hash changed")
    _validate_canonical_release_tree_descriptor(descriptor.read_bytes())
    if sha256_bytes(raw) != sha256_file(receipt_path)[0]:  # pragma: no cover - defensive read consistency.
        raise SourceAdoptionInstallError("source-adoption install receipt changed while reading")
    return {
        "candidate": candidate,
        "receipt_path": receipt_path,
        "receipt_sha256": sha256_bytes(raw),
        "package_id": package_id,
        "campaign_id": campaign_id,
        "application": application,
        "tooling": tooling,
        "canonical_release_tree_sha256": descriptor_sha,
        "files": files,
        "package": dict(package),
    }


def _require_safe_projection_path(value: str) -> PurePosixPath:
    pure = PurePosixPath(value)
    if pure.as_posix() != value or not value or value.startswith("/") or ".." in pure.parts:
        raise SourceAdoptionInstallError("runtime source projection path is unsafe")
    return pure


def _verify_projection_subtree(
    *,
    runtime_root: Path,
    relative: str,
    descriptor_files: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    pure = _require_safe_projection_path(relative)
    target = runtime_root / pure
    try:
        state = target.lstat()
    except OSError as exc:
        raise SourceAdoptionInstallError("runtime source projection is absent") from exc
    prefix = relative + "/"
    expected = {path: item for path, item in descriptor_files.items() if path == relative or path.startswith(prefix)}
    if not expected:
        raise SourceAdoptionInstallError("canonical release descriptor does not cover a runtime source projection")
    observed: set[str] = set()
    if stat.S_ISDIR(state.st_mode):
        for root_text, directories, filenames in os.walk(target, topdown=True, followlinks=False):
            root = Path(root_text)
            for directory in directories:
                item = root / directory
                metadata = item.lstat()
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
                    raise SourceAdoptionInstallError("runtime source projection contains an unsafe directory")
            for filename in filenames:
                item = root / filename
                path = item.relative_to(runtime_root).as_posix()
                metadata = item.lstat()
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
                    raise SourceAdoptionInstallError("runtime source projection contains an unsafe file")
                observed.add(path)
    elif stat.S_ISREG(state.st_mode):
        if state.st_uid != 0 or stat.S_IMODE(state.st_mode) & 0o022:
            raise SourceAdoptionInstallError("runtime source projection contains an unsafe file")
        observed.add(relative)
    else:
        raise SourceAdoptionInstallError("runtime source projection is not a regular file or directory")
    if observed != set(expected):
        raise SourceAdoptionInstallError(
            "runtime source projection has extra or missing files; a separate static asset descriptor is required for non-Git runtime inputs"
        )
    normalized: list[dict[str, Any]] = []
    for path in sorted(expected):
        item = runtime_root / path
        actual_sha, actual_bytes = sha256_file(item)
        descriptor = expected[path]
        if actual_sha != descriptor["sha256"] or actual_bytes != descriptor["bytes"]:
            raise SourceAdoptionInstallError("runtime source projection hash does not match canonical release descriptor")
        metadata = item.lstat()
        if bool(stat.S_IMODE(metadata.st_mode) & 0o111) != (descriptor["mode"] == "100755"):
            raise SourceAdoptionInstallError("runtime source projection executable mode does not match canonical release descriptor")
        normalized.append({"path": path, "sha256": actual_sha, "bytes": actual_bytes, "mode": descriptor["mode"]})
    return normalized


def verify_canonical_runtime_projection(
    *,
    candidate: Path,
    runtime_source_root: Path,
    expected_application: Mapping[str, str],
) -> dict[str, Any]:
    """Verify only the exact host files bind-mounted into the live app runtime.

    The legacy source directory is deliberately not treated as a Git checkout.
    It may be named ``current``; the proof comes from hashing each expected
    live mount projection against controller-supplied canonical 2c08 material.
    Other host files are irrelevant unless Docker binds them below ``/app``.
    """

    runtime_root = require_root_only_directory(runtime_source_root, field="runtime source root")
    descriptor_path = candidate / CANONICAL_RELEASE_TREE_MEMBER
    descriptor = _validate_canonical_release_tree_descriptor(descriptor_path.read_bytes())
    if descriptor["application"]["release_sha"] != expected_application["release_sha"]:
        raise SourceAdoptionInstallError("canonical release descriptor release is unexpected")
    descriptor_files = {item["path"]: item for item in descriptor["files"]}
    projections = {
        relative: _verify_projection_subtree(
            runtime_root=runtime_root,
            relative=relative,
            descriptor_files=descriptor_files,
        )
        for relative in RUNTIME_CODE_PROJECTION_RELATIVES
    }
    return {
        "runtime_source_root": str(runtime_root),
        "release_sha": descriptor["application"]["release_sha"],
        "git_tree": descriptor["application"]["git_tree"],
        "descriptor_sha256": sha256_file(descriptor_path)[0],
        "projections": projections,
        "projection_sha256": sha256_bytes(canonical_json_bytes(projections)),
    }


def _require_trusted_executable(path: Path, *, field: str) -> Path:
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SourceAdoptionInstallError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or state.st_uid != 0
        or stat.S_IMODE(state.st_mode) & 0o022
        or not stat.S_IMODE(state.st_mode) & 0o111
    ):
        raise SourceAdoptionInstallError(f"{field} must be a trusted root-owned executable")
    return resolved


def _docker_environment() -> dict[str, str]:
    return {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C", "TZ": "UTC", "DOCKER_CONTEXT": "default"}


def _normalize_mounts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise SourceAdoptionInstallError("source container mounts are invalid")
    mounts: list[dict[str, Any]] = []
    destinations: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise SourceAdoptionInstallError("source container mount is invalid")
        mount_type = item.get("Type")
        source = item.get("Source")
        destination = item.get("Destination")
        writable = item.get("RW")
        if mount_type not in {"bind", "volume", "tmpfs"}:
            raise SourceAdoptionInstallError("source container mount type is invalid")
        if not isinstance(destination, str) or not destination.startswith("/") or "\x00" in destination:
            raise SourceAdoptionInstallError("source container mount destination is invalid")
        if destination in destinations:
            raise SourceAdoptionInstallError("source container repeats a mount destination")
        destinations.add(destination)
        if mount_type == "bind":
            if not isinstance(source, str) or not source.startswith("/") or "\x00" in source:
                raise SourceAdoptionInstallError("source bind mount source is invalid")
        elif source is not None and (not isinstance(source, str) or "\x00" in source):
            raise SourceAdoptionInstallError("source container mount source is invalid")
        if not isinstance(writable, bool):
            raise SourceAdoptionInstallError("source container mount write flag is invalid")
        mounts.append({"type": mount_type, "source": source, "destination": destination, "read_only": not writable})
    return sorted(mounts, key=lambda item: (item["destination"], item["type"], str(item["source"])))


def _inspect_container(name: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        raise SourceAdoptionInstallError("source container name is invalid")
    docker = _require_trusted_executable(Path("/usr/bin/docker"), field="docker")
    try:
        result = subprocess.run(
            [str(docker), "inspect", "--type", "container", name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_docker_environment(),
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceAdoptionInstallError("source container inspection could not start") from exc
    if result.returncode != 0:
        raise SourceAdoptionInstallError("source container inspection failed")
    try:
        value = json.loads(result.stdout, object_pairs_hook=_strict_object)
    except json.JSONDecodeError as exc:
        raise SourceAdoptionInstallError("source container inspection returned invalid JSON") from exc
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], Mapping):
        raise SourceAdoptionInstallError("source container inspection returned an invalid shape")
    item = value[0]
    identifier = item.get("Id")
    image_id = item.get("Image")
    config = item.get("Config")
    state = item.get("State")
    mounts = _normalize_mounts(item.get("Mounts"))
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise SourceAdoptionInstallError("source container ID is invalid")
    if not isinstance(image_id, str) or not IMAGE_ID_RE.fullmatch(image_id):
        raise SourceAdoptionInstallError("source container image ID is invalid")
    if not isinstance(config, Mapping) or not isinstance(config.get("Image"), str) or not IMAGE_REFERENCE_RE.fullmatch(config["Image"]):
        raise SourceAdoptionInstallError("source container image reference is invalid")
    if not isinstance(state, Mapping) or state.get("Running") is not True:
        raise SourceAdoptionInstallError("source container must be running for read-only attestation")
    return {
        "name": name,
        "container_id": identifier,
        "image_id": image_id,
        "image_reference": config["Image"],
        "mounts": mounts,
    }


def _inspect_image(image_id: str) -> dict[str, Any]:
    if not IMAGE_ID_RE.fullmatch(image_id):
        raise SourceAdoptionInstallError("image ID is invalid")
    docker = _require_trusted_executable(Path("/usr/bin/docker"), field="docker")
    try:
        result = subprocess.run(
            [str(docker), "image", "inspect", image_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_docker_environment(),
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceAdoptionInstallError("source image inspection could not start") from exc
    if result.returncode != 0:
        raise SourceAdoptionInstallError("source image inspection failed")
    try:
        value = json.loads(result.stdout, object_pairs_hook=_strict_object)
    except json.JSONDecodeError as exc:
        raise SourceAdoptionInstallError("source image inspection returned invalid JSON") from exc
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], Mapping):
        raise SourceAdoptionInstallError("source image inspection returned an invalid shape")
    item = value[0]
    if item.get("Id") != image_id:
        raise SourceAdoptionInstallError("source image inspection does not bind the expected image ID")
    tags = item.get("RepoTags") or []
    digests = item.get("RepoDigests") or []
    if not isinstance(tags, list) or not isinstance(digests, list) or not all(isinstance(value, str) and IMAGE_REFERENCE_RE.fullmatch(value) for value in tags + digests):
        raise SourceAdoptionInstallError("source image inspection returned unsafe image references")
    return {"image_id": image_id, "repo_tags": sorted(tags), "repo_digests": sorted(digests)}


def _decode_pinned_public_key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise SourceAdoptionInstallError("pinned WA-FI source signing public key is invalid") from exc
    if len(decoded) != 32:
        raise SourceAdoptionInstallError("pinned WA-FI source signing public key has an invalid length")
    return decoded


def _public_key_id(value: str) -> str:
    """Stable non-secret key identifier for a separately enrolled Ed25519 key."""

    return "ed25519-sha256:" + sha256_bytes(_decode_pinned_public_key(value))


def _load_fi_signer(private_key_path: Path, *, pinned_public_key_base64: str) -> tuple[Any, str]:
    private_key_path = require_root_only_file(private_key_path, field="WA-FI source signing private key", maximum_bytes=32)
    raw = private_key_path.read_bytes()
    if len(raw) != 32:
        raise SourceAdoptionInstallError("WA-FI source signing private key must contain exactly 32 bytes")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise SourceAdoptionInstallError("cryptography Ed25519 support is unavailable") from exc
    try:
        signer = Ed25519PrivateKey.from_private_bytes(raw)
        public = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    except ValueError as exc:
        raise SourceAdoptionInstallError("WA-FI source signing private key is invalid") from exc
    if public != _decode_pinned_public_key(pinned_public_key_base64):
        raise SourceAdoptionInstallError("WA-FI source signing key does not match the pinned public key")
    return signer, base64.b64encode(public).decode("ascii")


def _verify_signature(*, unsigned: Mapping[str, Any], signature_base64: object, pinned_public_key_base64: str, domain: bytes) -> None:
    if not isinstance(signature_base64, str):
        raise SourceAdoptionInstallError("source attestation signature is invalid")
    try:
        signature = base64.b64decode(signature_base64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise SourceAdoptionInstallError("source attestation signature is invalid") from exc
    if len(signature) != 64:
        raise SourceAdoptionInstallError("source attestation signature has an invalid length")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise SourceAdoptionInstallError("cryptography Ed25519 support is unavailable") from exc
    try:
        Ed25519PublicKey.from_public_bytes(_decode_pinned_public_key(pinned_public_key_base64)).verify(
            signature,
            domain + canonical_json_bytes(unsigned),
        )
    except InvalidSignature as exc:
        raise SourceAdoptionInstallError("source attestation signature verification failed") from exc


def _require_campaign_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not CAMPAIGN_ID_RE.fullmatch(value):
        raise SourceAdoptionInstallError(f"{field} is invalid")
    return value


def _validate_signed_delivery_envelope(
    *,
    envelope: Path,
    pinned_controller_public_key_base64: str,
    expected_control_commit: str,
    expected_application: Mapping[str, str],
    expected_descriptor_sha256: str,
) -> dict[str, Any]:
    value, raw = _read_private_json(envelope, field="controller-signed source-adoption delivery envelope")
    expected = {
        "schema", "status", "campaign_id", "source_site", "destination_site", "package_id", "application", "tooling",
        "canonical_release_tree_sha256", "fi_bootstrap_recipient", "object", "controller_public_key_base64", "controller_signature",
    }
    if set(value) != expected or value.get("schema") != DELIVERY_ENVELOPE_SCHEMA or value.get("status") != "issued":
        raise SourceAdoptionInstallError("controller-signed source-adoption delivery envelope is unsupported")
    if value.get("source_site") != PACKAGE_SOURCE_SITE or value.get("destination_site") != PACKAGE_DESTINATION_SITE:
        raise SourceAdoptionInstallError("controller-signed source-adoption delivery envelope site binding is invalid")
    campaign_id = _require_campaign_id(value.get("campaign_id"), field="controller-signed source-adoption delivery envelope campaign_id")
    package_id = _require_package_id(value.get("package_id"), field="controller-signed source-adoption delivery envelope package_id")
    application = _require_application(value.get("application"), field="controller-signed source-adoption delivery envelope application")
    tooling = _require_tooling(value.get("tooling"), field="controller-signed source-adoption delivery envelope tooling")
    if tooling["control_commit"] != expected_control_commit or application != dict(expected_application):
        raise SourceAdoptionInstallError("controller-signed source-adoption delivery envelope release binding is unexpected")
    if _require_sha256(value.get("canonical_release_tree_sha256"), field="controller-signed source-adoption delivery envelope descriptor sha256") != expected_descriptor_sha256:
        raise SourceAdoptionInstallError("controller-signed source-adoption delivery envelope projection descriptor is unexpected")
    recipient = value.get("fi_bootstrap_recipient")
    if not isinstance(recipient, str) or not re.fullmatch(r"age1[ac-hj-np-z02-9]{20,128}", recipient):
        raise SourceAdoptionInstallError("controller-signed source-adoption delivery envelope recipient is invalid")
    object_value = value.get("object")
    expected_object = {"object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes"}
    if not isinstance(object_value, Mapping) or set(object_value) != expected_object:
        raise SourceAdoptionInstallError("controller-signed source-adoption delivery envelope object is invalid")
    object_key = object_value.get("object_key")
    version_id = object_value.get("version_id")
    if not isinstance(object_key, str) or not OBJECT_KEY_RE.fullmatch(object_key):
        raise SourceAdoptionInstallError("controller-signed source-adoption delivery envelope object key is invalid")
    if not isinstance(version_id, str) or not version_id or len(version_id) > 1024:
        raise SourceAdoptionInstallError("controller-signed source-adoption delivery envelope version ID is invalid")
    normalized_object = {
        "object_key": object_key,
        "version_id": version_id,
        "ciphertext_sha256": _require_sha256(object_value.get("ciphertext_sha256"), field="controller-signed source-adoption delivery envelope ciphertext sha256"),
        "ciphertext_bytes": _require_size(object_value.get("ciphertext_bytes"), field="controller-signed source-adoption delivery envelope ciphertext bytes", maximum=MAX_ARCHIVE_BYTES + 1024 * 1024),
        "plaintext_sha256": _require_sha256(object_value.get("plaintext_sha256"), field="controller-signed source-adoption delivery envelope plaintext sha256"),
        "plaintext_bytes": _require_size(object_value.get("plaintext_bytes"), field="controller-signed source-adoption delivery envelope plaintext bytes", maximum=MAX_ARCHIVE_BYTES),
    }
    if value.get("controller_public_key_base64") != pinned_controller_public_key_base64:
        raise SourceAdoptionInstallError("controller-signed source-adoption delivery envelope key is not pinned")
    signature = value.get("controller_signature")
    if not isinstance(signature, Mapping) or set(signature) != {"algorithm", "signature_base64"} or signature.get("algorithm") != "ed25519":
        raise SourceAdoptionInstallError("controller-signed source-adoption delivery envelope signature is invalid")
    _verify_signature(
        unsigned={key: item for key, item in value.items() if key != "controller_signature"},
        signature_base64=signature.get("signature_base64"),
        pinned_public_key_base64=pinned_controller_public_key_base64,
        domain=DELIVERY_ENVELOPE_SIGNATURE_DOMAIN,
    )
    return {
        "campaign_id": campaign_id,
        "package_id": package_id,
        "tooling": tooling,
        "application": application,
        "recipient": recipient,
        "object": normalized_object,
        "sha256": sha256_bytes(raw),
        "controller_public_key_base64": pinned_controller_public_key_base64,
    }


def _load_fi_signer_from_role_config(role_config: Mapping[str, Any], *, pinned_public_key_base64: str) -> tuple[Any, str]:
    return _load_fi_signer(
        role_config["source_signing_private_key_file"],
        pinned_public_key_base64=pinned_public_key_base64,
    )


def _validate_signer_enrollment_certificate(
    *,
    certificate: Path,
    pinned_controller_public_key_base64: str,
    campaign_id: str,
    installed: Mapping[str, Any],
    role_config: Mapping[str, Any],
    ssh_host_public_key_file: Path,
    verification_time: str | None = None,
) -> dict[str, Any]:
    """Validate one controller-authorized, short-lived FI signing enrollment.

    The certificate intentionally binds every locally observed input that the
    source key may later sign.  It is not a generic trust-on-first-use key
    enrollment and it is not reusable once consumed below the local staging
    root.
    """

    value, raw = _read_private_json(certificate, field="WebApp-FI signer enrollment certificate")
    expected = {
        "schema", "status", "certificate_id", "operation_id", "issued_at", "not_before", "not_after",
        "campaign_id", "source_site", "destination_site", "package_id", "application", "tooling",
        "canonical_release_tree_sha256", "source_adoption_install_receipt_sha256", "delivery_envelope_sha256",
        "source_adoption_object", "fi_bootstrap_recipient", "fi_ssh_host_public_key_sha256",
        "source_signing_public_key_base64", "source_signing_key_id", "controller_public_key_base64",
        "controller_key_id", "controller_signature",
    }
    if set(value) != expected or value.get("schema") != SIGNER_ENROLLMENT_CERTIFICATE_SCHEMA or value.get("status") != "issued":
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate is unsupported")
    certificate_id = _require_attestation_id(value.get("certificate_id"))
    operation_id = _require_attestation_id(value.get("operation_id"))
    issued_at = _parse_utc_timestamp(value.get("issued_at"), field="WebApp-FI signer enrollment certificate issued_at")
    not_before = _parse_utc_timestamp(value.get("not_before"), field="WebApp-FI signer enrollment certificate not_before")
    not_after = _parse_utc_timestamp(value.get("not_after"), field="WebApp-FI signer enrollment certificate not_after")
    now = _parse_utc_timestamp(verification_time or utc_now(), field="WebApp-FI signer enrollment verification time")
    if issued_at > not_before or not_before > not_after or (not_after - issued_at).total_seconds() > MAX_ENROLLMENT_CERTIFICATE_LIFETIME_SECONDS:
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate lifetime is invalid")
    if now < not_before or now > not_after:
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate is not currently valid")
    if value.get("campaign_id") != campaign_id or value.get("source_site") != PACKAGE_DESTINATION_SITE or value.get("destination_site") != SNAPSHOT_DESTINATION_SITE:
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate binding is invalid")
    if (
        value.get("package_id") != installed["package_id"]
        or _require_application(value.get("application"), field="WebApp-FI signer enrollment certificate application") != installed["application"]
        or _require_tooling(value.get("tooling"), field="WebApp-FI signer enrollment certificate tooling") != installed["tooling"]
        or _require_sha256(value.get("canonical_release_tree_sha256"), field="WebApp-FI signer enrollment certificate descriptor sha256") != installed["canonical_release_tree_sha256"]
        or _require_sha256(value.get("source_adoption_install_receipt_sha256"), field="WebApp-FI signer enrollment certificate install receipt sha256") != installed["receipt_sha256"]
        or _require_sha256(value.get("delivery_envelope_sha256"), field="WebApp-FI signer enrollment certificate delivery envelope sha256") != installed["package"]["delivery_envelope_sha256"]
    ):
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate package binding is invalid")
    object_value = value.get("source_adoption_object")
    object_expected = {"object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes"}
    if not isinstance(object_value, Mapping) or set(object_value) != object_expected:
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate object binding is invalid")
    normalized_object = {
        "object_key": object_value.get("object_key"),
        "version_id": object_value.get("version_id"),
        "ciphertext_sha256": _require_sha256(object_value.get("ciphertext_sha256"), field="WebApp-FI signer enrollment certificate ciphertext sha256"),
        "ciphertext_bytes": _require_size(object_value.get("ciphertext_bytes"), field="WebApp-FI signer enrollment certificate ciphertext bytes", maximum=MAX_ARCHIVE_BYTES + 1024 * 1024),
        "plaintext_sha256": _require_sha256(object_value.get("plaintext_sha256"), field="WebApp-FI signer enrollment certificate plaintext sha256"),
        "plaintext_bytes": _require_size(object_value.get("plaintext_bytes"), field="WebApp-FI signer enrollment certificate plaintext bytes", maximum=MAX_ARCHIVE_BYTES),
    }
    if not isinstance(normalized_object["object_key"], str) or not OBJECT_KEY_RE.fullmatch(normalized_object["object_key"]) or not isinstance(normalized_object["version_id"], str) or not normalized_object["version_id"] or len(normalized_object["version_id"]) > 1024:
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate object binding is invalid")
    expected_object = {
        "object_key": installed["package"]["object_key"],
        "version_id": installed["package"]["version_id"],
        "ciphertext_sha256": installed["package"]["ciphertext_sha256"],
        "ciphertext_bytes": installed["package"]["ciphertext_bytes"],
        "plaintext_sha256": installed["package"]["archive_sha256"],
        "plaintext_bytes": installed["package"]["archive_bytes"],
    }
    if normalized_object != expected_object or value.get("fi_bootstrap_recipient") != installed["package"]["fi_bootstrap_recipient"]:
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate delivery target is invalid")
    ssh_public = require_root_only_file(ssh_host_public_key_file, field="WebApp-FI SSH host public key")
    if _require_sha256(value.get("fi_ssh_host_public_key_sha256"), field="WebApp-FI signer enrollment certificate SSH host key sha256") != sha256_file(ssh_public)[0]:
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate is not bound to this SSH host identity")
    certificate_public = value.get("source_signing_public_key_base64")
    if not isinstance(certificate_public, str):
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate public key is invalid")
    signer, local_public = _load_fi_signer_from_role_config(role_config, pinned_public_key_base64=certificate_public)
    del signer
    if local_public != certificate_public:
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate does not bind the local key")
    if value.get("source_signing_key_id") != _public_key_id(certificate_public):
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate source key ID is invalid")
    if value.get("controller_public_key_base64") != pinned_controller_public_key_base64 or value.get("controller_key_id") != _public_key_id(pinned_controller_public_key_base64):
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate controller key is not pinned")
    signature = value.get("controller_signature")
    if not isinstance(signature, Mapping) or set(signature) != {"algorithm", "signature_base64"} or signature.get("algorithm") != "ed25519":
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate signature is invalid")
    _verify_signature(
        unsigned={key: item for key, item in value.items() if key != "controller_signature"},
        signature_base64=signature.get("signature_base64"),
        pinned_public_key_base64=pinned_controller_public_key_base64,
        domain=SIGNER_ENROLLMENT_SIGNATURE_DOMAIN,
    )
    return {
        "campaign_id": campaign_id,
        "certificate_id": certificate_id,
        "operation_id": operation_id,
        "not_after": value["not_after"],
        "source_signing_public_key_base64": certificate_public,
        "source_signing_key_id": value["source_signing_key_id"],
        "certificate_sha256": sha256_bytes(raw),
        "controller_public_key_base64": pinned_controller_public_key_base64,
        "controller_key_id": value["controller_key_id"],
        "delivery_envelope_sha256": value["delivery_envelope_sha256"],
        "source_adoption_object": normalized_object,
        "fi_bootstrap_recipient": value["fi_bootstrap_recipient"],
    }


def _validate_active_app_image(
    *,
    app_container: Mapping[str, str],
    expected_image_id: str,
    expected_image_reference: str,
) -> dict[str, Any]:
    if not IMAGE_ID_RE.fullmatch(expected_image_id):
        raise SourceAdoptionInstallError("expected active application image ID is invalid")
    if not IMAGE_REFERENCE_RE.fullmatch(expected_image_reference):
        raise SourceAdoptionInstallError("expected active application image reference is invalid")
    if app_container["image_id"] != expected_image_id or app_container["image_reference"] != expected_image_reference:
        raise SourceAdoptionInstallError("active WebApp-FI application container does not match the exact requested image")
    image = _inspect_image(expected_image_id)
    if expected_image_reference not in set(image["repo_tags"]) | set(image["repo_digests"]):
        raise SourceAdoptionInstallError("active WebApp-FI image reference is not bound by local image metadata")
    return {"image_id": expected_image_id, "image_reference": expected_image_reference, **image}


def _validate_mount_projection(
    *,
    container: Mapping[str, Any],
    runtime_source_root: Path,
    allow_static_assets: bool,
) -> list[dict[str, Any]]:
    """Require the live `/app` bind map to equal the reviewed source projection."""

    mounts = container.get("mounts")
    if not isinstance(mounts, list):
        raise SourceAdoptionInstallError("source container mount map is invalid")
    expected_code = {
        "/app/" + relative: str(runtime_source_root / relative)
        for relative in RUNTIME_CODE_PROJECTION_RELATIVES
    }
    if allow_static_assets:
        expected_code["/app/" + RUNTIME_STATIC_ASSET_RELATIVE] = str(runtime_source_root / RUNTIME_STATIC_ASSET_RELATIVE)
    code_mounts: dict[str, dict[str, Any]] = {}
    data_mounts: list[dict[str, Any]] = []
    for item in mounts:
        if not isinstance(item, Mapping):
            raise SourceAdoptionInstallError("source container mount map is invalid")
        destination = item.get("destination")
        if not isinstance(destination, str):
            raise SourceAdoptionInstallError("source container mount destination is invalid")
        if destination in expected_code:
            if item.get("type") != "bind" or item.get("source") != expected_code[destination]:
                raise SourceAdoptionInstallError("runtime code mount does not match the reviewed source projection")
            if destination in code_mounts:
                raise SourceAdoptionInstallError("runtime code mount is duplicated")
            code_mounts[destination] = dict(item)
            continue
        if destination.startswith("/app/"):
            if destination not in RUNTIME_DATA_MOUNT_TARGETS:
                raise SourceAdoptionInstallError("container has an unexpected bind or volume below /app")
            data_mounts.append(dict(item))
    if set(code_mounts) != set(expected_code):
        raise SourceAdoptionInstallError("container is missing an expected runtime source projection mount")
    normalized = [code_mounts[key] for key in sorted(code_mounts)]
    normalized.extend(sorted(data_mounts, key=lambda item: item["destination"]))
    return normalized


def _validate_static_assets_proof(
    *,
    static_assets_descriptor: Path,
    runtime_source_root: Path,
    expected_application: Mapping[str, str],
    pinned_controller_public_key_base64: str,
    campaign_id: str,
) -> dict[str, Any]:
    """Verify controller-authenticated deterministic assets, never an observation-only hash."""

    value, raw = _read_private_json(static_assets_descriptor, field="WebApp-FI static asset provenance")
    expected = {
        "schema", "status", "campaign_id", "application", "source_kind", "artifact", "files", "files_sha256",
        "controller_public_key_base64", "controller_signature",
    }
    if set(value) != expected or value.get("schema") != STATIC_ASSET_PROOF_SCHEMA or value.get("status") != "verified":
        raise SourceAdoptionInstallError("WebApp-FI static asset provenance is unsupported")
    if value.get("campaign_id") != campaign_id:
        raise SourceAdoptionInstallError("WebApp-FI static asset provenance campaign is unexpected")
    application = _require_application(value.get("application"), field="WebApp-FI static asset provenance application")
    if application != dict(expected_application):
        raise SourceAdoptionInstallError("WebApp-FI static asset provenance application binding is unexpected")
    if value.get("source_kind") != "deterministic_2c08_dist_manifest":
        raise SourceAdoptionInstallError("WebApp-FI static asset provenance is not derived from a deterministic release manifest")
    artifact = value.get("artifact")
    expected_artifact = {"object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes"}
    if not isinstance(artifact, Mapping) or set(artifact) != expected_artifact:
        raise SourceAdoptionInstallError("WebApp-FI static asset provenance artifact is invalid")
    object_key = artifact.get("object_key")
    version_id = artifact.get("version_id")
    if not isinstance(object_key, str) or not OBJECT_KEY_RE.fullmatch(object_key):
        raise SourceAdoptionInstallError("WebApp-FI static asset provenance object key is invalid")
    if not isinstance(version_id, str) or not version_id or len(version_id) > 1024:
        raise SourceAdoptionInstallError("WebApp-FI static asset provenance version ID is invalid")
    for name in ("ciphertext_sha256", "plaintext_sha256"):
        _require_sha256(artifact.get(name), field=f"WebApp-FI static asset provenance {name}")
    for name in ("ciphertext_bytes", "plaintext_bytes"):
        _require_size(artifact.get(name), field=f"WebApp-FI static asset provenance {name}", maximum=MAX_IMAGE_EXPORT_BYTES)
    raw_files = value.get("files")
    if not isinstance(raw_files, list):
        raise SourceAdoptionInstallError("WebApp-FI static asset provenance files are invalid")
    files: list[dict[str, Any]] = []
    prior = ""
    for item in raw_files:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256", "bytes"}:
            raise SourceAdoptionInstallError("WebApp-FI static asset provenance file is invalid")
        path = item.get("path")
        if not isinstance(path, str):
            raise SourceAdoptionInstallError("WebApp-FI static asset provenance path is invalid")
        pure = _require_safe_projection_path(path)
        if prior and path <= prior:
            raise SourceAdoptionInstallError("WebApp-FI static asset provenance paths are not strictly ordered")
        prior = path
        files.append(
            {
                "path": pure.as_posix(),
                "sha256": _require_sha256(item.get("sha256"), field="WebApp-FI static asset provenance sha256"),
                "bytes": _require_size(item.get("bytes"), field="WebApp-FI static asset provenance bytes", maximum=MAX_IMAGE_EXPORT_BYTES, minimum=0),
            }
        )
    if value.get("files_sha256") != sha256_bytes(canonical_json_bytes(files)):
        raise SourceAdoptionInstallError("WebApp-FI static asset provenance file hash is invalid")
    if value.get("controller_public_key_base64") != pinned_controller_public_key_base64:
        raise SourceAdoptionInstallError("WebApp-FI static asset provenance controller key is not pinned")
    signature = value.get("controller_signature")
    if not isinstance(signature, Mapping) or set(signature) != {"algorithm", "signature_base64"} or signature.get("algorithm") != "ed25519":
        raise SourceAdoptionInstallError("WebApp-FI static asset provenance signature is invalid")
    _verify_signature(
        unsigned={key: item for key, item in value.items() if key != "controller_signature"},
        signature_base64=signature.get("signature_base64"),
        pinned_public_key_base64=pinned_controller_public_key_base64,
        domain=STATIC_ASSET_SIGNATURE_DOMAIN,
    )
    assets_root = runtime_source_root / RUNTIME_STATIC_ASSET_RELATIVE
    try:
        root_state = assets_root.lstat()
    except OSError as exc:
        raise SourceAdoptionInstallError("WebApp-FI static asset root is absent") from exc
    if stat.S_ISLNK(root_state.st_mode) or not stat.S_ISDIR(root_state.st_mode) or root_state.st_uid != 0 or stat.S_IMODE(root_state.st_mode) & 0o022:
        raise SourceAdoptionInstallError("WebApp-FI static asset root is unsafe")
    observed: set[str] = set()
    for root_text, directories, filenames in os.walk(assets_root, topdown=True, followlinks=False):
        root = Path(root_text)
        for directory in directories:
            state = (root / directory).lstat()
            if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode) or state.st_uid != 0 or stat.S_IMODE(state.st_mode) & 0o022:
                raise SourceAdoptionInstallError("WebApp-FI static asset directory is unsafe")
        for filename in filenames:
            path = root / filename
            state = path.lstat()
            if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode) or state.st_uid != 0 or stat.S_IMODE(state.st_mode) & 0o022:
                raise SourceAdoptionInstallError("WebApp-FI static asset file is unsafe")
            observed.add(path.relative_to(assets_root).as_posix())
    expected_files = {item["path"]: item for item in files}
    if observed != set(expected_files):
        raise SourceAdoptionInstallError("WebApp-FI static assets do not exactly match the deterministic provenance manifest")
    for relative, expected_file in expected_files.items():
        actual_sha, actual_bytes = sha256_file(assets_root / relative)
        if actual_sha != expected_file["sha256"] or actual_bytes != expected_file["bytes"]:
            raise SourceAdoptionInstallError("WebApp-FI static asset hash does not match deterministic provenance")
    return {
        "descriptor_sha256": sha256_bytes(raw),
        "artifact": dict(artifact),
        "files_sha256": value["files_sha256"],
        "file_count": len(files),
        "source_kind": value["source_kind"],
    }


def _require_container_name(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise SourceAdoptionInstallError(f"{field} is invalid")
    return value


def load_source_role_config(path: Path, *, expected_application: Mapping[str, str]) -> dict[str, Any]:
    value, raw = _read_private_json(path, field="WebApp-FI source role config")
    expected = {
        "schema", "source_site", "destination_site", "application", "application_container",
        "sync_worker_container", "source_signing_private_key_file",
    }
    if set(value) != expected or value.get("schema") != SOURCE_ROLE_CONFIG_SCHEMA:
        raise SourceAdoptionInstallError("WebApp-FI source role config is unsupported")
    if value.get("source_site") != PACKAGE_DESTINATION_SITE or value.get("destination_site") != SNAPSHOT_DESTINATION_SITE:
        raise SourceAdoptionInstallError("WebApp-FI source role config site binding is invalid")
    application = _require_application(value.get("application"), field="WebApp-FI source role config application")
    if application != dict(expected_application):
        raise SourceAdoptionInstallError("WebApp-FI source role config application binding is unexpected")
    signer = value.get("source_signing_private_key_file")
    if not isinstance(signer, str) or not signer.startswith("/"):
        raise SourceAdoptionInstallError("WebApp-FI source role config private reference is invalid")
    return {
        "path": require_root_only_file(path, field="WebApp-FI source role config"),
        "sha256": sha256_bytes(raw),
        "application_container": _require_container_name(value.get("application_container"), field="WebApp-FI application container"),
        "sync_worker_container": _require_container_name(value.get("sync_worker_container"), field="WebApp-FI sync worker container"),
        "source_signing_private_key_file": require_root_only_file(Path(signer), field="WebApp-FI source signing private key", maximum_bytes=32),
    }


def _write_attestation(candidate: Path, *, attestation_id: str, value: Mapping[str, Any]) -> Path:
    if not ATTESTATION_ID_RE.fullmatch(attestation_id):
        raise SourceAdoptionInstallError("attestation_id is invalid")
    directory = candidate / "attestations"
    if not directory.exists():
        _create_directory(directory)
    directory = require_root_only_directory(directory, field="source-adoption attestation directory")
    path = directory / f"{attestation_id}.json"
    if path.exists() or path.is_symlink():
        raise SourceAdoptionInstallError("source-adoption attestation already exists")
    _write_new_private_json(directory / path.name, value)
    return require_root_only_file(path, field="source-adoption attestation")


def _require_attestation_id(value: object) -> str:
    if not isinstance(value, str) or not ATTESTATION_ID_RE.fullmatch(value):
        raise SourceAdoptionInstallError("source attestation ID is invalid")
    return value


def _parse_utc_timestamp(value: object, *, field: str) -> dt.datetime:
    """Accept exactly a whole-second UTC RFC3339 timestamp.

    Signed operation artifacts must not accept offset aliases, fractional
    precision ambiguity, or timezone-naive values.  Returning the parsed
    instant keeps all lifetime checks on one representation.
    """

    if not isinstance(value, str) or not UTC_TIMESTAMP_RE.fullmatch(value):
        raise SourceAdoptionInstallError(f"{field} is invalid")
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise SourceAdoptionInstallError(f"{field} is invalid") from exc
    return parsed


def _require_timestamp(value: object, *, field: str) -> str:
    _parse_utc_timestamp(value, field=field)
    return value


def _require_positive_seconds(value: object, *, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise SourceAdoptionInstallError(f"{field} is invalid")
    return value


def _validate_recorded_mounts(value: object, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise SourceAdoptionInstallError(f"{field} is invalid")
    normalized: list[dict[str, Any]] = []
    destinations: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"type", "source", "destination", "read_only"}:
            raise SourceAdoptionInstallError(f"{field} is invalid")
        mount_type = item.get("type")
        source = item.get("source")
        destination = item.get("destination")
        read_only = item.get("read_only")
        if mount_type not in {"bind", "volume", "tmpfs"} or not isinstance(destination, str) or not destination.startswith("/") or "\x00" in destination:
            raise SourceAdoptionInstallError(f"{field} is invalid")
        if destination in destinations or not isinstance(read_only, bool):
            raise SourceAdoptionInstallError(f"{field} is invalid")
        destinations.add(destination)
        if mount_type == "bind":
            if not isinstance(source, str) or not source.startswith("/") or "\x00" in source:
                raise SourceAdoptionInstallError(f"{field} is invalid")
        elif source is not None and (not isinstance(source, str) or "\x00" in source):
            raise SourceAdoptionInstallError(f"{field} is invalid")
        normalized.append({"type": mount_type, "source": source, "destination": destination, "read_only": read_only})
    if normalized != sorted(normalized, key=lambda item: (item["destination"], item["type"], str(item["source"]))):
        raise SourceAdoptionInstallError(f"{field} is not normalized")
    return normalized


def _validate_recorded_container(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"name", "container_id", "image_id", "image_reference", "mounts"}:
        raise SourceAdoptionInstallError(f"{field} is invalid")
    name = _require_container_name(value.get("name"), field=f"{field} name")
    container_id = value.get("container_id")
    image_id = value.get("image_id")
    image_reference = value.get("image_reference")
    if not isinstance(container_id, str) or not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise SourceAdoptionInstallError(f"{field} container ID is invalid")
    if not isinstance(image_id, str) or not IMAGE_ID_RE.fullmatch(image_id):
        raise SourceAdoptionInstallError(f"{field} image ID is invalid")
    if not isinstance(image_reference, str) or not IMAGE_REFERENCE_RE.fullmatch(image_reference):
        raise SourceAdoptionInstallError(f"{field} image reference is invalid")
    return {"name": name, "container_id": container_id, "image_id": image_id, "image_reference": image_reference, "mounts": _validate_recorded_mounts(value.get("mounts"), field=f"{field} mounts")}


def _validate_projection_record(value: object, *, field: str, expected_application: Mapping[str, str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"runtime_source_root", "release_sha", "git_tree", "descriptor_sha256", "projections", "projection_sha256"}:
        raise SourceAdoptionInstallError(f"{field} is invalid")
    runtime_root = value.get("runtime_source_root")
    if not isinstance(runtime_root, str) or not runtime_root.startswith("/"):
        raise SourceAdoptionInstallError(f"{field} source root is invalid")
    if value.get("release_sha") != expected_application["release_sha"] or not isinstance(value.get("git_tree"), str) or not COMMIT_RE.fullmatch(value["git_tree"]):
        raise SourceAdoptionInstallError(f"{field} release binding is invalid")
    descriptor_sha = _require_sha256(value.get("descriptor_sha256"), field=f"{field} descriptor sha256")
    projections = value.get("projections")
    if not isinstance(projections, Mapping) or set(projections) != set(RUNTIME_CODE_PROJECTION_RELATIVES):
        raise SourceAdoptionInstallError(f"{field} projections are invalid")
    normalized: dict[str, list[dict[str, Any]]] = {}
    for relative in RUNTIME_CODE_PROJECTION_RELATIVES:
        raw_entries = projections.get(relative)
        if not isinstance(raw_entries, list) or not raw_entries:
            raise SourceAdoptionInstallError(f"{field} projection is invalid")
        entries: list[dict[str, Any]] = []
        previous = ""
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, Mapping) or set(raw_entry) != {"path", "sha256", "bytes", "mode"}:
                raise SourceAdoptionInstallError(f"{field} projection entry is invalid")
            path = raw_entry.get("path")
            if not isinstance(path, str) or (path != relative and not path.startswith(relative + "/")) or (previous and path <= previous):
                raise SourceAdoptionInstallError(f"{field} projection entry path is invalid")
            previous = path
            entries.append({
                "path": path,
                "sha256": _require_sha256(raw_entry.get("sha256"), field=f"{field} projection sha256"),
                "bytes": _require_size(raw_entry.get("bytes"), field=f"{field} projection bytes", maximum=MAX_IMAGE_EXPORT_BYTES, minimum=0),
                "mode": raw_entry.get("mode"),
            })
            if entries[-1]["mode"] not in {"100644", "100755"}:
                raise SourceAdoptionInstallError(f"{field} projection mode is invalid")
        normalized[relative] = entries
    if value.get("projection_sha256") != sha256_bytes(canonical_json_bytes(normalized)):
        raise SourceAdoptionInstallError(f"{field} projection hash is invalid")
    return {"runtime_source_root": runtime_root, "release_sha": expected_application["release_sha"], "git_tree": value["git_tree"], "descriptor_sha256": descriptor_sha, "projections": normalized, "projection_sha256": value["projection_sha256"]}


def _validate_static_proof_record(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"descriptor_sha256", "artifact", "files_sha256", "file_count", "source_kind"}:
        raise SourceAdoptionInstallError(f"{field} is invalid")
    if value.get("source_kind") != "deterministic_2c08_dist_manifest":
        raise SourceAdoptionInstallError(f"{field} is not deterministic release material")
    artifact = value.get("artifact")
    names = {"object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes"}
    if not isinstance(artifact, Mapping) or set(artifact) != names:
        raise SourceAdoptionInstallError(f"{field} artifact is invalid")
    if not isinstance(artifact.get("object_key"), str) or not OBJECT_KEY_RE.fullmatch(artifact["object_key"]):
        raise SourceAdoptionInstallError(f"{field} artifact object key is invalid")
    if not isinstance(artifact.get("version_id"), str) or not artifact["version_id"] or len(artifact["version_id"]) > 1024:
        raise SourceAdoptionInstallError(f"{field} artifact version is invalid")
    for key in ("ciphertext_sha256", "plaintext_sha256"):
        _require_sha256(artifact.get(key), field=f"{field} artifact {key}")
    for key in ("ciphertext_bytes", "plaintext_bytes"):
        _require_size(artifact.get(key), field=f"{field} artifact {key}", maximum=MAX_IMAGE_EXPORT_BYTES)
    count = _require_size(value.get("file_count"), field=f"{field} file count", maximum=MAX_CANONICAL_RELEASE_FILES, minimum=0)
    return {"descriptor_sha256": _require_sha256(value.get("descriptor_sha256"), field=f"{field} descriptor sha256"), "artifact": dict(artifact), "files_sha256": _require_sha256(value.get("files_sha256"), field=f"{field} files sha256"), "file_count": count, "source_kind": value["source_kind"]}


def _write_enrollment(candidate: Path, *, campaign_id: str, value: Mapping[str, Any]) -> Path:
    directory = candidate / "enrollments"
    if not directory.exists():
        _create_directory(directory)
    directory = require_root_only_directory(directory, field="source-adoption signer enrollment directory")
    path = directory / f"{campaign_id}.json"
    if path.exists() or path.is_symlink():
        raise SourceAdoptionInstallError("source-adoption signer enrollment already exists")
    _write_new_private_json(path, value)
    return require_root_only_file(path, field="source-adoption signer enrollment")


def _certificate_consumption_path(*, candidate: Path, certificate_id: str, create_directory: bool) -> Path:
    """Return the staging-root-local, create-only certificate consumption path."""

    _require_attestation_id(certificate_id)
    staging_root = require_root_only_directory(candidate.parent, field="source-adoption staging root")
    directory = staging_root / "certificate-consumptions"
    if create_directory and not directory.exists():
        _create_directory(directory)
    if directory.exists() or directory.is_symlink():
        directory = require_root_only_directory(directory, field="source-adoption certificate consumption directory")
    return directory / f"{certificate_id}.json"


def _validate_certificate_consumption(
    *,
    path: Path,
    certificate_value: Mapping[str, Any],
    certificate_sha256: str,
    candidate: Path,
    campaign_id: str,
) -> dict[str, Any]:
    value, raw = _read_private_json(path, field="WebApp-FI signer enrollment certificate consumption")
    expected = {
        "schema", "status", "consumed_at", "certificate_id", "operation_id", "certificate_sha256",
        "candidate_directory", "campaign_id", "source_signing_key_id", "receipt_sha256",
    }
    if set(value) != expected or value.get("schema") != SIGNER_ENROLLMENT_CONSUMPTION_SCHEMA or value.get("status") != "consumed":
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate consumption is unsupported")
    _parse_utc_timestamp(value.get("consumed_at"), field="WebApp-FI signer enrollment certificate consumption timestamp")
    receipt_sha = _require_sha256(value.get("receipt_sha256"), field="WebApp-FI signer enrollment certificate consumption receipt sha256")
    if sha256_bytes(canonical_json_bytes({key: item for key, item in value.items() if key != "receipt_sha256"})) != receipt_sha:
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate consumption receipt hash is invalid")
    if (
        value.get("certificate_id") != certificate_value["certificate_id"]
        or value.get("operation_id") != certificate_value["operation_id"]
        or _require_sha256(value.get("certificate_sha256"), field="WebApp-FI signer enrollment certificate consumption certificate sha256") != certificate_sha256
        or value.get("candidate_directory") != str(candidate)
        or value.get("campaign_id") != campaign_id
        or value.get("source_signing_key_id") != certificate_value["source_signing_key_id"]
    ):
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate consumption binding is invalid")
    return {"path": path, "sha256": sha256_bytes(raw)}


def _consume_enrollment_certificate(
    *,
    candidate: Path,
    certificate_value: Mapping[str, Any],
    certificate_sha256: str,
    campaign_id: str,
) -> dict[str, Any]:
    path = _certificate_consumption_path(
        candidate=candidate,
        certificate_id=certificate_value["certificate_id"],
        create_directory=True,
    )
    if path.exists() or path.is_symlink():
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment certificate was already consumed locally")
    receipt: dict[str, Any] = {
        "schema": SIGNER_ENROLLMENT_CONSUMPTION_SCHEMA,
        "status": "consumed",
        "consumed_at": utc_now(),
        "certificate_id": certificate_value["certificate_id"],
        "operation_id": certificate_value["operation_id"],
        "certificate_sha256": certificate_sha256,
        "candidate_directory": str(candidate),
        "campaign_id": campaign_id,
        "source_signing_key_id": certificate_value["source_signing_key_id"],
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    _write_new_private_json(path, receipt)
    return _validate_certificate_consumption(
        path=path,
        certificate_value=certificate_value,
        certificate_sha256=certificate_sha256,
        candidate=candidate,
        campaign_id=campaign_id,
    )


def verify_source_signer_enrollment(
    *,
    enrollment_receipt: Path,
    installed: Mapping[str, Any],
    role_config: Mapping[str, Any],
    certificate: Path,
    ssh_host_public_key_file: Path,
    pinned_controller_public_key_base64: str,
    campaign_id: str,
    verification_time: str | None = None,
) -> dict[str, Any]:
    value, raw = _read_private_json(enrollment_receipt, field="WebApp-FI source signer enrollment receipt")
    expected = {
        "schema", "status", "enrolled_at", "candidate_directory", "campaign_id", "source_site",
        "destination_site", "package_id", "application", "tooling", "canonical_release_tree_sha256",
        "source_adoption_install_receipt_sha256", "delivery_envelope_sha256", "certificate_id", "operation_id",
        "certificate_sha256", "certificate_consumption_sha256", "source_role_config_sha256",
        "fi_ssh_host_public_key_sha256", "controller_public_key_base64", "controller_key_id",
        "source_signing_public_key_base64", "source_signing_key_id", "not_after", "receipt_sha256",
    }
    if set(value) != expected or value.get("schema") != SIGNER_ENROLLMENT_RECEIPT_SCHEMA or value.get("status") != "enrolled":
        raise SourceAdoptionInstallError("WebApp-FI source signer enrollment receipt is unsupported")
    _require_timestamp(value.get("enrolled_at"), field="WebApp-FI source signer enrollment receipt timestamp")
    receipt_sha = _require_sha256(value.get("receipt_sha256"), field="WebApp-FI source signer enrollment receipt sha256")
    if sha256_bytes(canonical_json_bytes({key: item for key, item in value.items() if key != "receipt_sha256"})) != receipt_sha:
        raise SourceAdoptionInstallError("WebApp-FI source signer enrollment receipt hash is invalid")
    campaign_id = _require_campaign_id(campaign_id, field="campaign_id")
    candidate = installed["candidate"]
    if value.get("candidate_directory") != str(candidate) or enrollment_receipt != candidate / "enrollments" / f"{campaign_id}.json":
        raise SourceAdoptionInstallError("WebApp-FI source signer enrollment receipt is not candidate-bound")
    if value.get("campaign_id") != campaign_id or value.get("source_site") != PACKAGE_DESTINATION_SITE or value.get("destination_site") != SNAPSHOT_DESTINATION_SITE:
        raise SourceAdoptionInstallError("WebApp-FI source signer enrollment receipt binding is invalid")
    if (
        value.get("package_id") != installed["package_id"]
        or _require_application(value.get("application"), field="WebApp-FI source signer enrollment receipt application") != installed["application"]
        or _require_tooling(value.get("tooling"), field="WebApp-FI source signer enrollment receipt tooling") != installed["tooling"]
        or _require_sha256(value.get("canonical_release_tree_sha256"), field="WebApp-FI source signer enrollment receipt descriptor sha256") != installed["canonical_release_tree_sha256"]
        or _require_sha256(value.get("source_adoption_install_receipt_sha256"), field="WebApp-FI source signer enrollment receipt install receipt sha256") != installed["receipt_sha256"]
        or _require_sha256(value.get("delivery_envelope_sha256"), field="WebApp-FI source signer enrollment receipt delivery envelope sha256") != installed["package"]["delivery_envelope_sha256"]
    ):
        raise SourceAdoptionInstallError("WebApp-FI source signer enrollment receipt release binding is invalid")
    certificate_value = _validate_signer_enrollment_certificate(
        certificate=certificate,
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        campaign_id=campaign_id,
        installed=installed,
        role_config=role_config,
        ssh_host_public_key_file=ssh_host_public_key_file,
        verification_time=verification_time,
    )
    ssh_hash = sha256_file(require_root_only_file(ssh_host_public_key_file, field="WebApp-FI SSH host public key"))[0]
    consumption_path = _certificate_consumption_path(
        candidate=candidate,
        certificate_id=certificate_value["certificate_id"],
        create_directory=False,
    )
    consumption = _validate_certificate_consumption(
        path=consumption_path,
        certificate_value=certificate_value,
        certificate_sha256=certificate_value["certificate_sha256"],
        candidate=candidate,
        campaign_id=campaign_id,
    )
    if (
        value.get("certificate_id") != certificate_value["certificate_id"]
        or value.get("operation_id") != certificate_value["operation_id"]
        or value.get("source_role_config_sha256") != role_config["sha256"]
        or value.get("fi_ssh_host_public_key_sha256") != ssh_hash
        or value.get("certificate_sha256") != certificate_value["certificate_sha256"]
        or value.get("certificate_consumption_sha256") != consumption["sha256"]
        or value.get("controller_public_key_base64") != pinned_controller_public_key_base64
        or value.get("controller_key_id") != certificate_value["controller_key_id"]
        or value.get("source_signing_public_key_base64") != certificate_value["source_signing_public_key_base64"]
        or value.get("source_signing_key_id") != certificate_value["source_signing_key_id"]
        or value.get("not_after") != certificate_value["not_after"]
    ):
        raise SourceAdoptionInstallError("WebApp-FI source signer enrollment receipt does not match trusted enrollment material")
    return {"status": "verified", "receipt_sha256": sha256_bytes(raw), "certificate_consumption_sha256": consumption["sha256"], **certificate_value}


def enroll_source_signer(
    *,
    install_receipt: Path,
    source_role_config: Path,
    certificate: Path,
    ssh_host_public_key_file: Path,
    pinned_controller_public_key_base64: str,
    campaign_id: str,
    apply: bool,
    verification_time: str | None = None,
) -> dict[str, Any]:
    _require_root_execution()
    installed = verify_installed_source_adoption(install_receipt)
    campaign_id = _require_campaign_id(campaign_id, field="campaign_id")
    if installed["campaign_id"] != campaign_id or installed["package"]["controller_public_key_base64"] != pinned_controller_public_key_base64:
        raise SourceAdoptionInstallError("WebApp-FI signer enrollment controller or campaign is not bound to the installed package")
    role_config = load_source_role_config(source_role_config, expected_application=installed["application"])
    certificate_value = _validate_signer_enrollment_certificate(
        certificate=certificate,
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        campaign_id=campaign_id,
        installed=installed,
        role_config=role_config,
        ssh_host_public_key_file=ssh_host_public_key_file,
        verification_time=verification_time,
    )
    path = installed["candidate"] / "enrollments" / f"{campaign_id}.json"
    consumption_path = _certificate_consumption_path(
        candidate=installed["candidate"],
        certificate_id=certificate_value["certificate_id"],
        create_directory=False,
    )
    plan = {
        "schema": SIGNER_ENROLLMENT_RECEIPT_SCHEMA,
        "status": "planned" if not apply else "enrolling",
        "candidate_directory": str(installed["candidate"]),
        "campaign_id": campaign_id,
        "certificate_id": certificate_value["certificate_id"],
        "operation_id": certificate_value["operation_id"],
        "certificate_sha256": certificate_value["certificate_sha256"],
        "certificate_consumption_path": str(consumption_path),
        "source_signing_public_key_base64": certificate_value["source_signing_public_key_base64"],
        "private_key_creation": False,
        "current_changed": False,
        "service_changed": False,
        "container_changed": False,
        "volume_changed": False,
        "application_data_changed": False,
    }
    if not apply:
        return plan
    if path.exists() or path.is_symlink():
        raise SourceAdoptionInstallError("WebApp-FI source signer enrollment already exists")
    consumption = _consume_enrollment_certificate(
        candidate=installed["candidate"],
        certificate_value=certificate_value,
        certificate_sha256=certificate_value["certificate_sha256"],
        campaign_id=campaign_id,
    )
    ssh_hash = sha256_file(require_root_only_file(ssh_host_public_key_file, field="WebApp-FI SSH host public key"))[0]
    receipt: dict[str, Any] = {
        "schema": SIGNER_ENROLLMENT_RECEIPT_SCHEMA,
        "status": "enrolled",
        "enrolled_at": utc_now(),
        "candidate_directory": str(installed["candidate"]),
        "campaign_id": campaign_id,
        "source_site": PACKAGE_DESTINATION_SITE,
        "destination_site": SNAPSHOT_DESTINATION_SITE,
        "package_id": installed["package_id"],
        "application": installed["application"],
        "tooling": installed["tooling"],
        "canonical_release_tree_sha256": installed["canonical_release_tree_sha256"],
        "source_adoption_install_receipt_sha256": installed["receipt_sha256"],
        "delivery_envelope_sha256": installed["package"]["delivery_envelope_sha256"],
        "certificate_id": certificate_value["certificate_id"],
        "operation_id": certificate_value["operation_id"],
        "certificate_sha256": certificate_value["certificate_sha256"],
        "certificate_consumption_sha256": consumption["sha256"],
        "source_role_config_sha256": role_config["sha256"],
        "fi_ssh_host_public_key_sha256": ssh_hash,
        "controller_public_key_base64": pinned_controller_public_key_base64,
        "controller_key_id": certificate_value["controller_key_id"],
        "source_signing_public_key_base64": certificate_value["source_signing_public_key_base64"],
        "source_signing_key_id": certificate_value["source_signing_key_id"],
        "not_after": certificate_value["not_after"],
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    enrollment_path = _write_enrollment(installed["candidate"], campaign_id=campaign_id, value=receipt)
    return verify_source_signer_enrollment(
        enrollment_receipt=enrollment_path,
        installed=installed,
        role_config=role_config,
        certificate=certificate,
        ssh_host_public_key_file=ssh_host_public_key_file,
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        campaign_id=campaign_id,
        verification_time=verification_time,
    )


def _validate_active_runtime_images(*, application: Mapping[str, Any], sync_worker: Mapping[str, Any], expected_image_id: str, expected_image_reference: str) -> dict[str, Any]:
    image = _validate_active_app_image(app_container=application, expected_image_id=expected_image_id, expected_image_reference=expected_image_reference)
    if sync_worker.get("image_id") != expected_image_id or sync_worker.get("image_reference") != expected_image_reference:
        raise SourceAdoptionInstallError("active WebApp-FI sync worker does not match the exact requested application image")
    return image


def attest_source_role(
    *,
    install_receipt: Path,
    source_role_config: Path,
    signer_enrollment_receipt: Path,
    signer_enrollment_certificate: Path,
    ssh_host_public_key_file: Path,
    runtime_source_root: Path,
    static_assets_descriptor: Path,
    pinned_controller_public_key_base64: str,
    campaign_id: str,
    expected_app_image_id: str,
    expected_app_image_reference: str,
    attestation_id: str,
    apply: bool,
) -> dict[str, Any]:
    _require_root_execution()
    installed = verify_installed_source_adoption(install_receipt)
    campaign_id = _require_campaign_id(campaign_id, field="campaign_id")
    if installed["campaign_id"] != campaign_id or installed["package"]["controller_public_key_base64"] != pinned_controller_public_key_base64:
        raise SourceAdoptionInstallError("WebApp-FI source attestation controller or campaign is not bound to the installed package")
    role_config = load_source_role_config(source_role_config, expected_application=installed["application"])
    enrollment = verify_source_signer_enrollment(enrollment_receipt=signer_enrollment_receipt, installed=installed, role_config=role_config, certificate=signer_enrollment_certificate, ssh_host_public_key_file=ssh_host_public_key_file, pinned_controller_public_key_base64=pinned_controller_public_key_base64, campaign_id=campaign_id)
    runtime_before = verify_canonical_runtime_projection(candidate=installed["candidate"], runtime_source_root=runtime_source_root, expected_application=installed["application"])
    static_before = _validate_static_assets_proof(static_assets_descriptor=static_assets_descriptor, runtime_source_root=runtime_source_root, expected_application=installed["application"], pinned_controller_public_key_base64=pinned_controller_public_key_base64, campaign_id=campaign_id)
    plan = {
        "schema": ATTESTATION_SCHEMA,
        "status": "planned" if not apply else "attesting",
        "campaign_id": campaign_id,
        "application": installed["application"],
        "tooling": installed["tooling"],
        "canonical_release_tree_sha256": installed["canonical_release_tree_sha256"],
        "runtime_projection_sha256": runtime_before["projection_sha256"],
        "static_assets_proof_sha256": static_before["descriptor_sha256"],
        "expected_active_app_image_id": expected_app_image_id,
        "expected_active_app_image_reference": expected_app_image_reference,
        "point_in_time_observation_only": True,
        "data_or_schema_capture_performed": False,
        "requires_app_and_sync_mount_race_check": True,
        "current_changed": False,
        "service_changed": False,
        "container_changed": False,
        "volume_changed": False,
        "application_data_changed": False,
        "direct_webapp_fi_to_webapp_ir_transfer": False,
    }
    if not apply:
        return plan
    application_before = _inspect_container(role_config["application_container"])
    sync_before = _inspect_container(role_config["sync_worker_container"])
    _validate_mount_projection(container=application_before, runtime_source_root=runtime_source_root, allow_static_assets=True)
    _validate_mount_projection(container=sync_before, runtime_source_root=runtime_source_root, allow_static_assets=False)
    active_image_before = _validate_active_runtime_images(application=application_before, sync_worker=sync_before, expected_image_id=expected_app_image_id, expected_image_reference=expected_app_image_reference)
    runtime_after = verify_canonical_runtime_projection(candidate=installed["candidate"], runtime_source_root=runtime_source_root, expected_application=installed["application"])
    static_after = _validate_static_assets_proof(static_assets_descriptor=static_assets_descriptor, runtime_source_root=runtime_source_root, expected_application=installed["application"], pinned_controller_public_key_base64=pinned_controller_public_key_base64, campaign_id=campaign_id)
    application_after = _inspect_container(role_config["application_container"])
    sync_after = _inspect_container(role_config["sync_worker_container"])
    _validate_mount_projection(container=application_after, runtime_source_root=runtime_source_root, allow_static_assets=True)
    _validate_mount_projection(container=sync_after, runtime_source_root=runtime_source_root, allow_static_assets=False)
    active_image_after = _validate_active_runtime_images(application=application_after, sync_worker=sync_after, expected_image_id=expected_app_image_id, expected_image_reference=expected_app_image_reference)
    if runtime_before != runtime_after or static_before != static_after or application_before != application_after or sync_before != sync_after or active_image_before != active_image_after:
        raise SourceAdoptionInstallError("WebApp-FI source changed during attestation; unsafe candidate is retained without an attestation")
    signer, public_key = _load_fi_signer_from_role_config(role_config, pinned_public_key_base64=enrollment["source_signing_public_key_base64"])
    package = installed["package"]
    source_adoption_delivery = {
        "object_key": package["object_key"],
        "version_id": package["version_id"],
        "ciphertext_sha256": package["ciphertext_sha256"],
        "ciphertext_bytes": package["ciphertext_bytes"],
        "plaintext_sha256": package["archive_sha256"],
        "plaintext_bytes": package["archive_bytes"],
        "delivery_envelope_sha256": package["delivery_envelope_sha256"],
        "controller_public_key_base64": package["controller_public_key_base64"],
    }
    unsigned: dict[str, Any] = {
        "schema": ATTESTATION_SCHEMA,
        "status": "attested",
        "attested_at": utc_now(),
        "campaign_id": campaign_id,
        "source_site": PACKAGE_DESTINATION_SITE,
        "destination_site": SNAPSHOT_DESTINATION_SITE,
        "package_id": installed["package_id"],
        "application": installed["application"],
        "application_release_tree": runtime_before["git_tree"],
        "tooling": installed["tooling"],
        "source_adoption_install_receipt_sha256": installed["receipt_sha256"],
        "source_adoption_delivery": source_adoption_delivery,
        "canonical_release_tree_sha256": installed["canonical_release_tree_sha256"],
        "source_signer_enrollment": {
            "receipt_sha256": enrollment["receipt_sha256"],
            "certificate_sha256": enrollment["certificate_sha256"],
            "certificate_id": enrollment["certificate_id"],
            "operation_id": enrollment["operation_id"],
            "certificate_consumption_sha256": enrollment["certificate_consumption_sha256"],
            "not_after": enrollment["not_after"],
            "fi_ssh_host_public_key_sha256": sha256_file(require_root_only_file(ssh_host_public_key_file, field="WebApp-FI SSH host public key"))[0],
            "controller_key_id": enrollment["controller_key_id"],
            "source_signing_public_key_base64": public_key,
            "source_signing_key_id": enrollment["source_signing_key_id"],
        },
        "observation_scope": {
            "point_in_time_only": True,
            "data_capture_performed": False,
            "schema_capture_performed": False,
            "promotion_ready": False,
            "later_snapshot_requires_separate_authorization": True,
        },
        "runtime_projection": {"before": runtime_before, "after": runtime_after},
        "static_assets_proof": {"before": static_before, "after": static_after, "proof_is_not_static_payload": True, "promotion_requires_verified_immutable_age_object": True},
        "containers": {"application": application_before, "sync_worker": sync_before},
        "active_application_image": active_image_before,
        "race_check": {
            "runtime_projection_unchanged": True,
            "static_assets_unchanged": True,
            "application_container_unchanged": True,
            "sync_worker_container_unchanged": True,
            "active_image_unchanged": True,
        },
        "source_signing_public_key_base64": public_key,
        "source_signing_key_id": _public_key_id(public_key),
    }
    signature = signer.sign(ATTESTATION_SIGNATURE_DOMAIN + canonical_json_bytes(unsigned))
    receipt = {**unsigned, "source_signature": {"algorithm": "ed25519", "signature_base64": base64.b64encode(signature).decode("ascii")}}
    path = _write_attestation(installed["candidate"], attestation_id=attestation_id, value=receipt)
    return verify_source_role_attestation(
        attestation=path,
        pinned_source_signing_public_key_base64=public_key,
        expected_campaign_id=campaign_id,
        expected_application=installed["application"],
        expected_control_commit=installed["tooling"]["control_commit"],
        expected_canonical_release_tree_sha256=installed["canonical_release_tree_sha256"],
        expected_app_image_id=expected_app_image_id,
        expected_app_image_reference=expected_app_image_reference,
    )


def verify_source_role_attestation(
    *,
    attestation: Path,
    pinned_source_signing_public_key_base64: str,
    expected_campaign_id: str,
    expected_application: Mapping[str, str],
    expected_control_commit: str,
    expected_canonical_release_tree_sha256: str,
    expected_app_image_id: str,
    expected_app_image_reference: str,
) -> dict[str, Any]:
    """Verify FI's signature and normalize only its point-in-time claims.

    This helper verifies an FI source key.  It deliberately does not treat
    fields naming a controller key, package delivery, or enrollment as proof
    of controller authorization; that proof is separately established by the
    local certificate consumption path and by controller-side consumers.
    """

    expected_campaign_id = _require_campaign_id(expected_campaign_id, field="expected_campaign_id")
    expected_application = _require_application(expected_application, field="expected_application")
    expected_descriptor = _require_sha256(expected_canonical_release_tree_sha256, field="expected canonical release descriptor sha256")
    if not COMMIT_RE.fullmatch(expected_control_commit) or not IMAGE_ID_RE.fullmatch(expected_app_image_id) or not IMAGE_REFERENCE_RE.fullmatch(expected_app_image_reference):
        raise SourceAdoptionInstallError("expected source attestation binding is invalid")
    value, raw = _read_private_json(attestation, field="WebApp-FI source role attestation")
    expected = {
        "schema", "status", "attested_at", "campaign_id", "source_site", "destination_site", "package_id",
        "application", "application_release_tree", "tooling", "source_adoption_install_receipt_sha256",
        "source_adoption_delivery", "canonical_release_tree_sha256", "source_signer_enrollment",
        "observation_scope", "runtime_projection", "static_assets_proof", "containers",
        "active_application_image", "race_check", "source_signing_public_key_base64", "source_signing_key_id",
        "source_signature",
    }
    if set(value) != expected or value.get("schema") != ATTESTATION_SCHEMA or value.get("status") != "attested":
        raise SourceAdoptionInstallError("WebApp-FI source role attestation is unsupported")
    _require_timestamp(value.get("attested_at"), field="WebApp-FI source role attestation timestamp")
    if value.get("campaign_id") != expected_campaign_id or value.get("source_site") != PACKAGE_DESTINATION_SITE or value.get("destination_site") != SNAPSHOT_DESTINATION_SITE:
        raise SourceAdoptionInstallError("WebApp-FI source role attestation site or campaign binding is invalid")
    package_id = _require_package_id(value.get("package_id"), field="WebApp-FI source role attestation package ID")
    application = _require_application(value.get("application"), field="WebApp-FI source role attestation application")
    tooling = _require_tooling(value.get("tooling"), field="WebApp-FI source role attestation tooling")
    if application != expected_application or tooling["control_commit"] != expected_control_commit:
        raise SourceAdoptionInstallError("WebApp-FI source role attestation release binding is unexpected")
    _require_sha256(value.get("source_adoption_install_receipt_sha256"), field="WebApp-FI source role attestation install receipt sha256")
    delivery = value.get("source_adoption_delivery")
    delivery_expected = {"object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes", "delivery_envelope_sha256", "controller_public_key_base64"}
    if not isinstance(delivery, Mapping) or set(delivery) != delivery_expected or not isinstance(delivery.get("object_key"), str) or not OBJECT_KEY_RE.fullmatch(delivery["object_key"]) or not isinstance(delivery.get("version_id"), str) or not delivery["version_id"] or len(delivery["version_id"]) > 1024:
        raise SourceAdoptionInstallError("WebApp-FI source role attestation adoption delivery is invalid")
    for field in ("ciphertext_sha256", "plaintext_sha256", "delivery_envelope_sha256"):
        _require_sha256(delivery.get(field), field=f"WebApp-FI source role attestation adoption delivery {field}")
    for field in ("ciphertext_bytes", "plaintext_bytes"):
        _require_size(delivery.get(field), field=f"WebApp-FI source role attestation adoption delivery {field}", maximum=MAX_ARCHIVE_BYTES + 1024 * 1024)
    if not isinstance(delivery.get("controller_public_key_base64"), str):
        raise SourceAdoptionInstallError("WebApp-FI source role attestation adoption delivery controller key is invalid")
    _decode_pinned_public_key(delivery["controller_public_key_base64"])
    descriptor_sha = _require_sha256(value.get("canonical_release_tree_sha256"), field="WebApp-FI source role attestation descriptor sha256")
    if descriptor_sha != expected_descriptor:
        raise SourceAdoptionInstallError("WebApp-FI source role attestation descriptor is unexpected")
    if not isinstance(value.get("application_release_tree"), str) or not COMMIT_RE.fullmatch(value["application_release_tree"]):
        raise SourceAdoptionInstallError("WebApp-FI source role attestation application release tree is invalid")
    enrollment = value.get("source_signer_enrollment")
    enrollment_expected = {
        "receipt_sha256", "certificate_sha256", "certificate_id", "operation_id", "certificate_consumption_sha256",
        "not_after", "fi_ssh_host_public_key_sha256", "controller_key_id", "source_signing_public_key_base64",
        "source_signing_key_id",
    }
    if not isinstance(enrollment, Mapping) or set(enrollment) != enrollment_expected:
        raise SourceAdoptionInstallError("WebApp-FI source role attestation signer enrollment is invalid")
    for field in ("receipt_sha256", "certificate_sha256", "certificate_consumption_sha256", "fi_ssh_host_public_key_sha256"):
        _require_sha256(enrollment.get(field), field=f"WebApp-FI source role attestation enrollment {field}")
    _require_attestation_id(enrollment.get("certificate_id"))
    _require_attestation_id(enrollment.get("operation_id"))
    _require_timestamp(enrollment.get("not_after"), field="WebApp-FI source role attestation enrollment not_after")
    if not isinstance(enrollment.get("controller_key_id"), str) or not enrollment["controller_key_id"].startswith("ed25519-sha256:") or not isinstance(enrollment.get("source_signing_public_key_base64"), str):
        raise SourceAdoptionInstallError("WebApp-FI source role attestation signer enrollment key is invalid")
    if enrollment["source_signing_public_key_base64"] != pinned_source_signing_public_key_base64 or value.get("source_signing_public_key_base64") != pinned_source_signing_public_key_base64:
        raise SourceAdoptionInstallError("WebApp-FI source role attestation signing key is not enrolled and pinned")
    if value.get("source_signing_key_id") != _public_key_id(pinned_source_signing_public_key_base64) or enrollment.get("source_signing_key_id") != value.get("source_signing_key_id"):
        raise SourceAdoptionInstallError("WebApp-FI source role attestation signing key ID is invalid")
    if value.get("observation_scope") != {
        "point_in_time_only": True,
        "data_capture_performed": False,
        "schema_capture_performed": False,
        "promotion_ready": False,
        "later_snapshot_requires_separate_authorization": True,
    }:
        raise SourceAdoptionInstallError("WebApp-FI source role attestation observation scope is invalid")
    before = _validate_projection_record(value.get("runtime_projection", {}).get("before") if isinstance(value.get("runtime_projection"), Mapping) else None, field="WebApp-FI source role attestation runtime projection before", expected_application=application)
    after = _validate_projection_record(value.get("runtime_projection", {}).get("after") if isinstance(value.get("runtime_projection"), Mapping) else None, field="WebApp-FI source role attestation runtime projection after", expected_application=application)
    if before != after or before["descriptor_sha256"] != descriptor_sha or before["git_tree"] != value["application_release_tree"]:
        raise SourceAdoptionInstallError("WebApp-FI source role attestation runtime projection race proof is invalid")
    static_value = value.get("static_assets_proof")
    if not isinstance(static_value, Mapping) or set(static_value) != {"before", "after", "proof_is_not_static_payload", "promotion_requires_verified_immutable_age_object"} or static_value.get("proof_is_not_static_payload") is not True or static_value.get("promotion_requires_verified_immutable_age_object") is not True:
        raise SourceAdoptionInstallError("WebApp-FI source role attestation static asset policy is invalid")
    static_before = _validate_static_proof_record(static_value.get("before"), field="WebApp-FI source role attestation static assets before")
    static_after = _validate_static_proof_record(static_value.get("after"), field="WebApp-FI source role attestation static assets after")
    if static_before != static_after:
        raise SourceAdoptionInstallError("WebApp-FI source role attestation static assets changed during proof")
    containers = value.get("containers")
    if not isinstance(containers, Mapping) or set(containers) != {"application", "sync_worker"}:
        raise SourceAdoptionInstallError("WebApp-FI source role attestation containers are invalid")
    app = _validate_recorded_container(containers["application"], field="WebApp-FI source role attestation application container")
    sync = _validate_recorded_container(containers["sync_worker"], field="WebApp-FI source role attestation sync worker container")
    runtime_root = Path(before["runtime_source_root"])
    _validate_mount_projection(container=app, runtime_source_root=runtime_root, allow_static_assets=True)
    _validate_mount_projection(container=sync, runtime_source_root=runtime_root, allow_static_assets=False)
    if app["image_id"] != expected_app_image_id or app["image_reference"] != expected_app_image_reference or sync["image_id"] != expected_app_image_id or sync["image_reference"] != expected_app_image_reference:
        raise SourceAdoptionInstallError("WebApp-FI source role attestation app or sync image is unexpected")
    active = value.get("active_application_image")
    if not isinstance(active, Mapping) or set(active) != {"image_id", "image_reference", "repo_tags", "repo_digests"}:
        raise SourceAdoptionInstallError("WebApp-FI source role attestation active image is invalid")
    repo_tags = active.get("repo_tags")
    repo_digests = active.get("repo_digests")
    if not isinstance(repo_tags, list) or not isinstance(repo_digests, list) or not all(isinstance(item, str) and IMAGE_REFERENCE_RE.fullmatch(item) for item in repo_tags + repo_digests):
        raise SourceAdoptionInstallError("WebApp-FI source role attestation active image references are invalid")
    if active.get("image_id") != expected_app_image_id or active.get("image_reference") != expected_app_image_reference or expected_app_image_reference not in set(repo_tags) | set(repo_digests):
        raise SourceAdoptionInstallError("WebApp-FI source role attestation active image is unexpected")
    if value.get("race_check") != {
        "runtime_projection_unchanged": True,
        "static_assets_unchanged": True,
        "application_container_unchanged": True,
        "sync_worker_container_unchanged": True,
        "active_image_unchanged": True,
    }:
        raise SourceAdoptionInstallError("WebApp-FI source role attestation race check is invalid")
    signature = value.get("source_signature")
    if not isinstance(signature, Mapping) or set(signature) != {"algorithm", "signature_base64"} or signature.get("algorithm") != "ed25519":
        raise SourceAdoptionInstallError("WebApp-FI source role attestation signature envelope is invalid")
    _verify_signature(unsigned={key: item for key, item in value.items() if key != "source_signature"}, signature_base64=signature.get("signature_base64"), pinned_public_key_base64=pinned_source_signing_public_key_base64, domain=ATTESTATION_SIGNATURE_DOMAIN)
    descriptor_claim = {
        "canonical_release_tree_sha256": descriptor_sha,
        "application_release_tree": value["application_release_tree"],
        "application": application,
    }
    runtime_claim = {
        "projection": before,
        "static_assets": static_before,
        "containers": {"application": app, "sync_worker": sync},
    }
    image_claim = {"active_application_image": dict(active), "image_id": expected_app_image_id, "image_reference": expected_app_image_reference}
    return {
        "status": "verified",
        "attestation_path": str(attestation),
        "attestation_sha256": sha256_bytes(raw),
        "attested_at": value["attested_at"],
        "campaign_id": expected_campaign_id,
        "package_id": package_id,
        "application": application,
        "tooling": tooling,
        "descriptor_claim": descriptor_claim,
        "runtime_claim": runtime_claim,
        "image_claim": image_claim,
        "source_adoption_delivery_claim": dict(delivery),
        "source_signing_public_key_base64": pinned_source_signing_public_key_base64,
        "source_signing_key_id": value["source_signing_key_id"],
        "source_site": PACKAGE_DESTINATION_SITE,
        "destination_site": SNAPSHOT_DESTINATION_SITE,
        "point_in_time_observation_only": True,
        "controller_authorization_verified": False,
    }


def _export_exact_docker_save_bytes(*, archive: Path, expected_image_id: str) -> dict[str, Any]:
    """Run the trusted Docker binary and bind only the resulting exact bytes.

    A Docker save archive is not a stable semantic format contract for this
    campaign.  Parsing a handful of tar members cannot prove it is safe to
    load, nor can it establish all image semantics.  The authoritative source
    binding is the trusted ``docker image inspect`` before/after the exact
    ``docker save`` invocation.  The archive receives a byte hash only and is
    never Docker-loaded by this helper.
    """

    if not IMAGE_ID_RE.fullmatch(expected_image_id):
        raise SourceAdoptionInstallError("actual WebApp-FI image export image ID is invalid")
    if archive.exists() or archive.is_symlink():
        raise SourceAdoptionInstallError("actual WebApp-FI image archive destination already exists")
    docker = _require_trusted_executable(Path("/usr/bin/docker"), field="docker")
    docker_sha256, docker_bytes = sha256_file(docker)
    try:
        result = subprocess.run(
            [str(docker), "save", "--output", str(archive), expected_image_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_docker_environment(),
            timeout=1800,
            check=False,
            umask=0o077,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceAdoptionInstallError("actual WebApp-FI image export could not start") from exc
    if result.returncode != 0:
        raise SourceAdoptionInstallError("actual WebApp-FI image export failed")
    archive = require_root_only_file(archive, field="actual WebApp-FI image archive", maximum_bytes=MAX_IMAGE_EXPORT_BYTES)
    archive_sha256, archive_bytes = sha256_file(archive)
    return {
        "archive_sha256": archive_sha256,
        "archive_bytes": archive_bytes,
        "docker_save": {
            "command": ["docker", "save", "--output", archive.name, expected_image_id],
            "docker_executable_sha256": docker_sha256,
            "docker_executable_bytes": docker_bytes,
            "archive_semantics": "exact_bytes_only_unparsed",
            "docker_load_invoked": False,
            "loadability_claimed": False,
        },
    }


def _revalidate_export_runtime(
    *,
    installed: Mapping[str, Any],
    role_config: Mapping[str, Any],
    runtime_source_root: Path,
    static_assets_descriptor: Path,
    pinned_controller_public_key_base64: str,
    campaign_id: str,
    expected_app_image_id: str,
    expected_app_image_reference: str,
) -> dict[str, Any]:
    """Read the exact live FI source/image state without changing Docker."""

    projection = verify_canonical_runtime_projection(
        candidate=installed["candidate"],
        runtime_source_root=runtime_source_root,
        expected_application=installed["application"],
    )
    static_assets = _validate_static_assets_proof(
        static_assets_descriptor=static_assets_descriptor,
        runtime_source_root=runtime_source_root,
        expected_application=installed["application"],
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        campaign_id=campaign_id,
    )
    application = _inspect_container(role_config["application_container"])
    sync_worker = _inspect_container(role_config["sync_worker_container"])
    _validate_mount_projection(container=application, runtime_source_root=runtime_source_root, allow_static_assets=True)
    _validate_mount_projection(container=sync_worker, runtime_source_root=runtime_source_root, allow_static_assets=False)
    active_image = _validate_active_runtime_images(
        application=application,
        sync_worker=sync_worker,
        expected_image_id=expected_app_image_id,
        expected_image_reference=expected_app_image_reference,
    )
    return {
        "projection": projection,
        "static_assets": static_assets,
        "containers": {"application": application, "sync_worker": sync_worker},
        "active_application_image": active_image,
    }


def export_actual_fi_image(
    *,
    attestation: Path,
    source_role_config: Path,
    signer_enrollment_receipt: Path,
    signer_enrollment_certificate: Path,
    ssh_host_public_key_file: Path,
    runtime_source_root: Path,
    static_assets_descriptor: Path,
    pinned_controller_public_key_base64: str,
    pinned_source_signing_public_key_base64: str,
    expected_campaign_id: str,
    expected_application: Mapping[str, str],
    expected_control_commit: str,
    expected_canonical_release_tree_sha256: str,
    expected_app_image_id: str,
    expected_app_image_reference: str,
    destination: Path,
    export_id: str,
    apply: bool,
) -> dict[str, Any]:
    """Export exact FI image bytes only after a fresh full source recheck."""

    _require_root_execution()
    expected_application = _require_application(expected_application, field="expected image export application")
    expected_descriptor = _require_sha256(expected_canonical_release_tree_sha256, field="expected image export descriptor sha256")
    attestation = require_root_only_file(attestation, field="WebApp-FI source role attestation")
    candidate = require_root_only_directory(attestation.parent.parent, field="source-adoption installed candidate")
    if attestation.parent != candidate / "attestations":
        raise SourceAdoptionInstallError("WebApp-FI source role attestation is not candidate-bound for export")
    installed = verify_installed_source_adoption(candidate / INSTALL_RECEIPT_NAME)
    if installed["campaign_id"] != expected_campaign_id or installed["application"] != expected_application or installed["tooling"]["control_commit"] != expected_control_commit or installed["canonical_release_tree_sha256"] != expected_descriptor:
        raise SourceAdoptionInstallError("WebApp-FI image export installed candidate is not the expected operation")
    verified = verify_source_role_attestation(
        attestation=attestation,
        pinned_source_signing_public_key_base64=pinned_source_signing_public_key_base64,
        expected_campaign_id=expected_campaign_id,
        expected_application=expected_application,
        expected_control_commit=expected_control_commit,
        expected_canonical_release_tree_sha256=expected_descriptor,
        expected_app_image_id=expected_app_image_id,
        expected_app_image_reference=expected_app_image_reference,
    )
    if not ATTESTATION_ID_RE.fullmatch(export_id):
        raise SourceAdoptionInstallError("image export_id is invalid")
    attested_at = _parse_utc_timestamp(verified["attested_at"], field="WebApp-FI source attestation export freshness")
    export_start = _parse_utc_timestamp(utc_now(), field="WebApp-FI image export start time")
    if attested_at > export_start or (export_start - attested_at).total_seconds() > MAX_OBSERVATION_AGE_SECONDS:
        raise SourceAdoptionInstallError("WebApp-FI source attestation is too old for image export")
    role_config = load_source_role_config(source_role_config, expected_application=verified["application"])
    enrollment = verify_source_signer_enrollment(
        enrollment_receipt=signer_enrollment_receipt,
        installed=installed,
        role_config=role_config,
        certificate=signer_enrollment_certificate,
        ssh_host_public_key_file=ssh_host_public_key_file,
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        campaign_id=expected_campaign_id,
        verification_time=utc_now(),
    )
    if enrollment["source_signing_public_key_base64"] != pinned_source_signing_public_key_base64:
        raise SourceAdoptionInstallError("WebApp-FI image export signer enrollment differs from source attestation")
    signer, public_key = _load_fi_signer_from_role_config(role_config, pinned_public_key_base64=pinned_source_signing_public_key_base64)
    destination = _require_absolute(destination, field="image export destination")
    parent = require_root_only_directory(destination.parent, field="image export destination parent")
    if destination.exists() or destination.is_symlink() or destination.parent != parent:
        raise SourceAdoptionInstallError("image export destination must be a new child of a root-only directory")
    plan = {
        "schema": IMAGE_EXPORT_RECEIPT_SCHEMA,
        "status": "planned" if not apply else "exporting",
        "source_site": PACKAGE_DESTINATION_SITE,
        "destination_site": SNAPSHOT_DESTINATION_SITE,
        "campaign_id": expected_campaign_id,
        "application": verified["application"],
        "canonical_release_tree_sha256": expected_descriptor,
        "active_application_image": verified["image_claim"]["active_application_image"],
        "destination": str(destination),
        "object_storage_export_required": {"transport": "private_versioned_age_only", "create_only": True, "read_back_same_version_id": True, "direct_webapp_fi_to_webapp_ir_transfer": False},
        "revalidate_projection_static_containers_before_and_after_docker_save": True,
        "exact_bytes_only_unparsed_archive": True,
        "docker_load_invoked": False,
        "loadability_claimed": False,
        "service_changed": False,
        "container_changed": False,
        "current_changed": False,
        "volume_changed": False,
        "application_data_changed": False,
    }
    if not apply:
        return plan
    before = _revalidate_export_runtime(
        installed=installed,
        role_config=role_config,
        runtime_source_root=runtime_source_root,
        static_assets_descriptor=static_assets_descriptor,
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        campaign_id=expected_campaign_id,
        expected_app_image_id=expected_app_image_id,
        expected_app_image_reference=expected_app_image_reference,
    )
    if (
        before["projection"] != verified["runtime_claim"]["projection"]
        or before["static_assets"] != verified["runtime_claim"]["static_assets"]
        or before["containers"] != verified["runtime_claim"]["containers"]
        or before["active_application_image"] != verified["image_claim"]["active_application_image"]
    ):
        raise SourceAdoptionInstallError("WebApp-FI source/image state no longer matches the signed point-in-time attestation")
    _create_directory(destination)
    archive = destination / "webapp-fi-active-app-image.tar"
    archive_info = _export_exact_docker_save_bytes(archive=archive, expected_image_id=expected_app_image_id)
    after = _revalidate_export_runtime(
        installed=installed,
        role_config=role_config,
        runtime_source_root=runtime_source_root,
        static_assets_descriptor=static_assets_descriptor,
        pinned_controller_public_key_base64=pinned_controller_public_key_base64,
        campaign_id=expected_campaign_id,
        expected_app_image_id=expected_app_image_id,
        expected_app_image_reference=expected_app_image_reference,
    )
    if after != before:
        raise SourceAdoptionInstallError("WebApp-FI source/image runtime changed during exact-byte export; archive is retained without a receipt")
    unsigned: dict[str, Any] = {
        "schema": IMAGE_EXPORT_RECEIPT_SCHEMA,
        "status": "exported",
        "exported_at": utc_now(),
        "export_id": export_id,
        "campaign_id": expected_campaign_id,
        "source_site": PACKAGE_DESTINATION_SITE,
        "destination_site": SNAPSHOT_DESTINATION_SITE,
        "application": verified["application"],
        "application_release_tree": verified["descriptor_claim"]["application_release_tree"],
        "tooling": verified["tooling"],
        "canonical_release_tree_sha256": expected_descriptor,
        "source_role_attestation_sha256": verified["attestation_sha256"],
        "observation_scope": {
            "point_in_time_only": True,
            "data_capture_performed": False,
            "schema_capture_performed": False,
            "promotion_ready": False,
            "later_snapshot_requires_separate_authorization": True,
        },
        "image": {"image_id": expected_app_image_id, "image_reference": expected_app_image_reference, **archive_info},
        "pre_export_runtime": before,
        "post_export_runtime": after,
        "exact_byte_export": {
            "archive_is_unparsed_exact_bytes": True,
            "docker_load_invoked": False,
            "loadability_claimed": False,
            "bind_mounted_runtime_revalidated_before_and_after": True,
        },
        "archive_consumption": {
            "docker_load_prohibited": True,
            "fi_local_exact_byte_hash_before_age_encryption": True,
            "controller_read_back_exact_byte_hash_after_age_encryption": True,
            "raw_repo_tags_are_not_authorization": True,
        },
        "object_storage_export_required": {"transport": "private_versioned_age_only", "create_only": True, "read_back_same_version_id": True, "direct_webapp_fi_to_webapp_ir_transfer": False},
        "source_signing_public_key_base64": public_key,
        "source_signing_key_id": _public_key_id(public_key),
    }
    signature = signer.sign(IMAGE_EXPORT_SIGNATURE_DOMAIN + canonical_json_bytes(unsigned))
    receipt = {**unsigned, "source_signature": {"algorithm": "ed25519", "signature_base64": base64.b64encode(signature).decode("ascii")}}
    _write_new_private_json(destination / "image-export-receipt.json", receipt)
    return {
        "status": "exported",
        "archive_path": str(archive),
        "receipt_path": str(destination / "image-export-receipt.json"),
        "descriptor_claim": verified["descriptor_claim"],
        "runtime_claim": after,
        "image_claim": {"image_id": expected_app_image_id, "image_reference": expected_app_image_reference, **archive_info},
        "object_storage_export_required": unsigned["object_storage_export_required"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    install = actions.add_parser("install")
    install.add_argument("--archive", type=Path, required=True)
    install.add_argument("--preparation-receipt", type=Path, required=True)
    install.add_argument("--delivery-receipt", type=Path, required=True)
    install.add_argument("--delivery-envelope", type=Path, required=True)
    install.add_argument("--pinned-controller-public-key-base64", required=True)
    install.add_argument("--expected-campaign-id", required=True)
    install.add_argument("--expected-fi-bootstrap-recipient", required=True)
    install.add_argument("--staging-root", type=Path, required=True)
    install.add_argument("--expected-control-commit", required=True)
    install.add_argument("--expected-application-release-sha", required=True)
    install.add_argument("--apply", action="store_true")
    enroll = actions.add_parser("enroll-signer")
    enroll.add_argument("--install-receipt", type=Path, required=True)
    enroll.add_argument("--source-role-config", type=Path, required=True)
    enroll.add_argument("--certificate", type=Path, required=True)
    enroll.add_argument("--ssh-host-public-key-file", type=Path, required=True)
    enroll.add_argument("--pinned-controller-public-key-base64", required=True)
    enroll.add_argument("--campaign-id", required=True)
    enroll.add_argument("--apply", action="store_true")
    attest = actions.add_parser("attest")
    attest.add_argument("--install-receipt", type=Path, required=True)
    attest.add_argument("--source-role-config", type=Path, required=True)
    attest.add_argument("--signer-enrollment-receipt", type=Path, required=True)
    attest.add_argument("--signer-enrollment-certificate", type=Path, required=True)
    attest.add_argument("--ssh-host-public-key-file", type=Path, required=True)
    attest.add_argument("--runtime-source-root", type=Path, required=True)
    attest.add_argument("--static-assets-descriptor", type=Path, required=True)
    attest.add_argument("--pinned-controller-public-key-base64", required=True)
    attest.add_argument("--campaign-id", required=True)
    attest.add_argument("--expected-app-image-id", required=True)
    attest.add_argument("--expected-app-image-reference", required=True)
    attest.add_argument("--attestation-id", required=True)
    attest.add_argument("--apply", action="store_true")
    export = actions.add_parser("export-image")
    export.add_argument("--attestation", type=Path, required=True)
    export.add_argument("--source-role-config", type=Path, required=True)
    export.add_argument("--signer-enrollment-receipt", type=Path, required=True)
    export.add_argument("--signer-enrollment-certificate", type=Path, required=True)
    export.add_argument("--ssh-host-public-key-file", type=Path, required=True)
    export.add_argument("--runtime-source-root", type=Path, required=True)
    export.add_argument("--static-assets-descriptor", type=Path, required=True)
    export.add_argument("--pinned-controller-public-key-base64", required=True)
    export.add_argument("--pinned-source-signing-public-key-base64", required=True)
    export.add_argument("--expected-campaign-id", required=True)
    export.add_argument("--expected-application-release-sha", required=True)
    export.add_argument("--expected-alembic-revision", required=True)
    export.add_argument("--expected-control-commit", required=True)
    export.add_argument("--expected-canonical-release-tree-sha256", required=True)
    export.add_argument("--expected-app-image-id", required=True)
    export.add_argument("--expected-app-image-reference", required=True)
    export.add_argument("--destination", type=Path, required=True)
    export.add_argument("--export-id", required=True)
    export.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _require_root_execution()
        if args.action == "install":
            result = install_source_adoption(
                archive=args.archive,
                preparation_receipt=args.preparation_receipt,
                delivery_receipt=args.delivery_receipt,
                delivery_envelope=args.delivery_envelope,
                pinned_controller_public_key_base64=args.pinned_controller_public_key_base64,
                expected_campaign_id=args.expected_campaign_id,
                expected_fi_bootstrap_recipient=args.expected_fi_bootstrap_recipient,
                staging_root=args.staging_root,
                expected_control_commit=args.expected_control_commit,
                expected_application_release_sha=args.expected_application_release_sha,
                apply=args.apply,
            )
        elif args.action == "enroll-signer":
            result = enroll_source_signer(
                install_receipt=args.install_receipt,
                source_role_config=args.source_role_config,
                certificate=args.certificate,
                ssh_host_public_key_file=args.ssh_host_public_key_file,
                pinned_controller_public_key_base64=args.pinned_controller_public_key_base64,
                campaign_id=args.campaign_id,
                apply=args.apply,
            )
        elif args.action == "attest":
            result = attest_source_role(
                install_receipt=args.install_receipt,
                source_role_config=args.source_role_config,
                signer_enrollment_receipt=args.signer_enrollment_receipt,
                signer_enrollment_certificate=args.signer_enrollment_certificate,
                ssh_host_public_key_file=args.ssh_host_public_key_file,
                runtime_source_root=args.runtime_source_root,
                static_assets_descriptor=args.static_assets_descriptor,
                pinned_controller_public_key_base64=args.pinned_controller_public_key_base64,
                campaign_id=args.campaign_id,
                expected_app_image_id=args.expected_app_image_id,
                expected_app_image_reference=args.expected_app_image_reference,
                attestation_id=args.attestation_id,
                apply=args.apply,
            )
        else:
            result = export_actual_fi_image(
                attestation=args.attestation,
                source_role_config=args.source_role_config,
                signer_enrollment_receipt=args.signer_enrollment_receipt,
                signer_enrollment_certificate=args.signer_enrollment_certificate,
                ssh_host_public_key_file=args.ssh_host_public_key_file,
                runtime_source_root=args.runtime_source_root,
                static_assets_descriptor=args.static_assets_descriptor,
                pinned_controller_public_key_base64=args.pinned_controller_public_key_base64,
                pinned_source_signing_public_key_base64=args.pinned_source_signing_public_key_base64,
                expected_campaign_id=args.expected_campaign_id,
                expected_application={"release_sha": args.expected_application_release_sha, "expected_alembic_revision": args.expected_alembic_revision},
                expected_control_commit=args.expected_control_commit,
                expected_canonical_release_tree_sha256=args.expected_canonical_release_tree_sha256,
                expected_app_image_id=args.expected_app_image_id,
                expected_app_image_reference=args.expected_app_image_reference,
                destination=args.destination,
                export_id=args.export_id,
                apply=args.apply,
            )
    except SourceAdoptionInstallError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2
    print(canonical_json_bytes(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
