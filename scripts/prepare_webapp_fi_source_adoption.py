#!/usr/bin/env python3
"""Prepare a detached WebApp-FI source-adoption package.

The legacy WebApp-FI host must not be modified through its mutable
``current`` checkout in order to produce a standby source proof.  This command
therefore prepares a small, exact, root-only tool package from a clean control
repository.  The package contains only source-proof helpers and non-secret
configuration examples; it contains no credentials, age identity, private
key, database password, service unit, image, application release, or
data-plane capture/publish/restore helper.

This is a local preparation primitive.  It does not contact Object Storage,
SSH to WebApp-FI, invoke Docker, provision credentials, or install anything on
another host.  A later, separately authorised Object-Storage-only delivery
must bind the archive to one immutable object version before the matching
WebApp-FI installer will accept it.

Both write actions default to a no-write plan; only ``--apply`` creates a
package or a controller-signed delivery envelope.  The envelope signer is an
already-enrolled controller key reference, never a key generated here.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
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
DELIVERY_ENVELOPE_SCHEMA = "gold-trade-webapp-fi-source-adoption-delivery-envelope-v1"

DELIVERY_ENVELOPE_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-source-adoption-delivery-envelope-v1\x00"

PACKAGE_ARCHIVE_NAME = "webapp-fi-source-adoption.tar"
PACKAGE_MANIFEST_MEMBER = "source-adoption-package.json"
PREPARATION_RECEIPT_NAME = "source-adoption-preparation-receipt.json"

PACKAGE_SOURCE_SITE = "bot_fi"
PACKAGE_DESTINATION_SITE = "webapp_fi"
SNAPSHOT_DESTINATION_SITE = "webapp_ir"

MAX_SOURCE_FILE_BYTES = 4 * 1024 * 1024
MAX_PACKAGE_MEMBER_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 24 * 1024 * 1024
MAX_CANONICAL_RELEASE_FILES = 100_000

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
ALEMBIC_RE = re.compile(r"^[0-9a-f]{12}$")
PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OBJECT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/=-]{2,1023}$")
CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{20,128}$")

# This bootstrap is intentionally attest-only.  Snapshot capture, Object
# Storage publication, restore, and any other data-plane capability belong to
# a separately authorised later phase and are not shipped to WebApp-FI here.
SOURCE_PAYLOAD_FILES = (
    "scripts/install_webapp_fi_source_adoption.py",
    "deploy/production/webapp-fi-source-role.json.example",
)
CONTRACT_MEMBER = "config/source-adoption-contract.json"
CANONICAL_RELEASE_TREE_MEMBER = "config/canonical-release-tree.json"
PACKAGE_PAYLOAD_FILES = (*SOURCE_PAYLOAD_FILES, CONTRACT_MEMBER, CANONICAL_RELEASE_TREE_MEMBER)
PACKAGE_FILES = (*PACKAGE_PAYLOAD_FILES, PACKAGE_MANIFEST_MEMBER)


class SourceAdoptionPreparationError(RuntimeError):
    """The detached source-adoption package cannot be proven safe."""


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise SourceAdoptionPreparationError("source-adoption package operations must run as root")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SourceAdoptionPreparationError("JSON input contains duplicate keys")
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


def _require_absolute(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise SourceAdoptionPreparationError(f"{field} must be an absolute path")
    return path


def _require_root_directory(path: Path, *, field: str, private: bool) -> Path:
    path = _require_absolute(path, field=field)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
        resolved_state = resolved.lstat()
    except OSError as exc:
        raise SourceAdoptionPreparationError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(state.st_mode)
        or stat.S_ISLNK(resolved_state.st_mode)
        or not stat.S_ISDIR(resolved_state.st_mode)
    ):
        raise SourceAdoptionPreparationError(f"{field} must be one canonical non-symlink directory")
    forbidden = 0o077 if private else 0o022
    if resolved_state.st_uid != 0 or stat.S_IMODE(resolved_state.st_mode) & forbidden:
        level = "root-private" if private else "root-owned and not group/other writable"
        raise SourceAdoptionPreparationError(f"{field} must be {level}")
    return resolved


def _git_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def _run_git(repository: Path, arguments: Sequence[str]) -> bytes:
    command = [
        "/usr/bin/git",
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
            stderr=subprocess.PIPE,
            env=_git_environment(),
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SourceAdoptionPreparationError("cannot verify the exact source-adoption control repository") from exc
    return result.stdout


def _require_reported_git_directory(repository: Path, argument: str, field: str) -> Path:
    raw = _run_git(repository, ["rev-parse", "--path-format=absolute", argument]).strip()
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceAdoptionPreparationError(f"{field} is not UTF-8") from exc
    if not value or any(character in value for character in "\r\n\x00"):
        raise SourceAdoptionPreparationError(f"{field} is malformed")
    return _require_root_directory(Path(value), field=field, private=False)


def _require_control_source(repository: Path, control_commit: str) -> tuple[Path, str]:
    if not COMMIT_RE.fullmatch(control_commit):
        raise SourceAdoptionPreparationError("control_commit is invalid")
    repository = _require_root_directory(repository, field="control source repository", private=False)
    if _run_git(repository, ["rev-parse", "--is-inside-work-tree"]).strip() != b"true":
        raise SourceAdoptionPreparationError("control source repository is not a Git worktree")
    _require_reported_git_directory(repository, "--git-dir", "control source Git directory")
    _require_reported_git_directory(repository, "--git-common-dir", "control source common Git directory")
    if _run_git(repository, ["status", "--porcelain=v1", "--untracked-files=all"]):
        raise SourceAdoptionPreparationError("control source repository must be clean")
    head = _run_git(repository, ["rev-parse", "HEAD^{commit}"]).strip().decode("ascii")
    if head != control_commit:
        raise SourceAdoptionPreparationError("control source HEAD does not match control_commit")
    verified = _run_git(repository, ["rev-parse", "--verify", control_commit + "^{commit}"]).strip().decode("ascii")
    if verified != control_commit:
        raise SourceAdoptionPreparationError("control source lacks the requested control_commit")
    tree = _run_git(repository, ["rev-parse", control_commit + "^{tree}"]).strip().decode("ascii")
    if not COMMIT_RE.fullmatch(tree):
        raise SourceAdoptionPreparationError("control source tree identity is invalid")
    return repository, tree


def _source_file(repository: Path, control_commit: str, relative: str) -> bytes:
    pure = PurePosixPath(relative)
    if pure.as_posix() != relative or relative.startswith("/") or ".." in pure.parts:
        raise SourceAdoptionPreparationError("control source path is unsafe")
    payload = _run_git(repository, ["show", f"{control_commit}:{relative}"])
    if not payload or len(payload) > MAX_SOURCE_FILE_BYTES:
        raise SourceAdoptionPreparationError("required source-adoption file has an unsafe size")
    return payload


def _require_safe_release_path(value: bytes) -> str:
    try:
        path = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceAdoptionPreparationError("canonical application release path is not UTF-8") from exc
    pure = PurePosixPath(path)
    if (
        pure.as_posix() != path
        or not path
        or path.startswith("/")
        or ".." in pure.parts
        or any(not item or any(ord(character) < 0x20 for character in item) for item in pure.parts)
    ):
        raise SourceAdoptionPreparationError("canonical application release path is unsafe")
    return path


def build_canonical_release_tree_descriptor(
    *,
    application_source_repository: Path,
    application_release_sha: str,
) -> dict[str, Any]:
    """Describe trusted 2c08 Git material without relying on FI Git metadata.

    The resulting descriptor is intentionally independent from the legacy
    WebApp-FI ``current`` directory.  A source host can later hash a separate,
    detached root against it; if that root is absent or differs, attestation
    fails rather than inferring provenance from a non-Git deployment path.
    """

    if not RELEASE_RE.fullmatch(application_release_sha):
        raise SourceAdoptionPreparationError("application_release_sha is invalid")
    repository = _require_root_directory(
        application_source_repository,
        field="canonical application source repository",
        private=False,
    )
    if _run_git(repository, ["rev-parse", "--is-inside-work-tree"]).strip() != b"true":
        raise SourceAdoptionPreparationError("canonical application source repository is not a Git worktree")
    _require_reported_git_directory(repository, "--git-dir", "canonical application source Git directory")
    _require_reported_git_directory(repository, "--git-common-dir", "canonical application source common Git directory")
    if _run_git(repository, ["status", "--porcelain=v1", "--untracked-files=all"]):
        raise SourceAdoptionPreparationError("canonical application source repository must be clean")
    head = _run_git(repository, ["rev-parse", "HEAD^{commit}"]).strip().decode("ascii")
    if head != application_release_sha:
        raise SourceAdoptionPreparationError("canonical application source HEAD does not match application_release_sha")
    verified = _run_git(repository, ["rev-parse", "--verify", application_release_sha + "^{commit}"]).strip().decode("ascii")
    if verified != application_release_sha:
        raise SourceAdoptionPreparationError("canonical application source lacks the requested release")
    tree = _run_git(repository, ["rev-parse", application_release_sha + "^{tree}"]).strip().decode("ascii")
    if not COMMIT_RE.fullmatch(tree):
        raise SourceAdoptionPreparationError("canonical application source tree identity is invalid")
    raw = _run_git(repository, ["ls-tree", "-r", "-z", "--full-tree", application_release_sha])
    entries = [item for item in raw.split(b"\x00") if item]
    if not entries or len(entries) > MAX_CANONICAL_RELEASE_FILES:
        raise SourceAdoptionPreparationError("canonical application release has an unsafe file count")
    files: list[dict[str, Any]] = []
    paths: set[str] = set()
    for record in entries:
        try:
            header, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = header.decode("ascii").split(" ", 2)
        except (UnicodeDecodeError, ValueError) as exc:
            raise SourceAdoptionPreparationError("canonical application release tree entry is malformed") from exc
        if mode not in {"100644", "100755"} or object_type != "blob" or not COMMIT_RE.fullmatch(object_id):
            raise SourceAdoptionPreparationError("canonical application release must contain only regular tracked files")
        path = _require_safe_release_path(raw_path)
        if path in paths:
            raise SourceAdoptionPreparationError("canonical application release repeats a file path")
        paths.add(path)
        payload = _run_git(repository, ["cat-file", "blob", object_id])
        files.append({"path": path, "mode": mode, "sha256": sha256_bytes(payload), "bytes": len(payload)})
    descriptor: dict[str, Any] = {
        "schema": "gold-trade-webapp-fi-canonical-release-tree-v1",
        "status": "prepared",
        "application": {"release_sha": application_release_sha, "git_tree": tree},
        "files": files,
        "files_sha256": sha256_bytes(canonical_json_bytes(files)),
    }
    _validate_canonical_release_tree_descriptor(canonical_json_bytes(descriptor) + b"\n")
    return descriptor


def _require_new_package_directory(destination: Path) -> Path:
    destination = _require_absolute(destination, field="destination")
    if destination.name in {"", ".", ".."}:
        raise SourceAdoptionPreparationError("destination name is invalid")
    parent = _require_root_directory(destination.parent, field="destination parent", private=True)
    if destination.exists() or destination.is_symlink():
        raise SourceAdoptionPreparationError("destination must not already exist")
    try:
        destination.mkdir(mode=0o700)
        os.chmod(destination, 0o700)
        state = destination.lstat()
        resolved = destination.resolve(strict=True)
    except OSError as exc:
        raise SourceAdoptionPreparationError("cannot create a new source-adoption package directory") from exc
    if (
        resolved.parent != parent
        or resolved != destination
        or not stat.S_ISDIR(state.st_mode)
        or state.st_uid != 0
        or stat.S_IMODE(state.st_mode) != 0o700
    ):
        raise SourceAdoptionPreparationError("new source-adoption package directory is unsafe")
    return resolved


def _write_new_private_file(path: Path, value: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise SourceAdoptionPreparationError(f"cannot create source-adoption file: {path.name}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        # Preserve a failed fresh package for forensic review.  It is never
        # retried in place and this helper deliberately has no cleanup path.
        raise


def _write_deterministic_archive(path: Path, files: Mapping[str, bytes]) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise SourceAdoptionPreparationError("cannot create source-adoption archive") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            with tarfile.open(fileobj=handle, mode="w:", format=tarfile.PAX_FORMAT) as archive:
                for name in sorted(files):
                    payload = files[name]
                    entry = tarfile.TarInfo(name)
                    entry.size = len(payload)
                    entry.mode = 0o600
                    entry.uid = 0
                    entry.gid = 0
                    entry.uname = ""
                    entry.gname = ""
                    entry.mtime = 0
                    archive.addfile(entry, io.BytesIO(payload))
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        raise
    digest, size = sha256_file(path)
    if not 1 <= size <= MAX_ARCHIVE_BYTES:
        raise SourceAdoptionPreparationError("source-adoption archive has an unsafe size")
    return digest, size


def _parse_canonical_json(payload: bytes, *, field: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceAdoptionPreparationError(f"{field} is not valid strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise SourceAdoptionPreparationError(f"{field} must be a JSON object")
    if payload != canonical_json_bytes(value) + b"\n":
        raise SourceAdoptionPreparationError(f"{field} must use canonical JSON")
    return value


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise SourceAdoptionPreparationError(f"{field} is invalid")
    return value


def _require_size(value: object, *, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise SourceAdoptionPreparationError(f"{field} is invalid")
    return value


def _require_application(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"release_sha", "expected_alembic_revision"}:
        raise SourceAdoptionPreparationError(f"{field} is invalid")
    release = value.get("release_sha")
    revision = value.get("expected_alembic_revision")
    if not isinstance(release, str) or not RELEASE_RE.fullmatch(release):
        raise SourceAdoptionPreparationError(f"{field}.release_sha is invalid")
    if not isinstance(revision, str) or not ALEMBIC_RE.fullmatch(revision):
        raise SourceAdoptionPreparationError(f"{field}.expected_alembic_revision is invalid")
    return {"release_sha": release, "expected_alembic_revision": revision}


def _require_tooling(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"control_commit", "control_tree"}:
        raise SourceAdoptionPreparationError(f"{field} is invalid")
    commit = value.get("control_commit")
    tree = value.get("control_tree")
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise SourceAdoptionPreparationError(f"{field}.control_commit is invalid")
    if not isinstance(tree, str) or not COMMIT_RE.fullmatch(tree):
        raise SourceAdoptionPreparationError(f"{field}.control_tree is invalid")
    return {"control_commit": commit, "control_tree": tree}


def _require_package_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not PACKAGE_ID_RE.fullmatch(value):
        raise SourceAdoptionPreparationError(f"{field} is invalid")
    return value


def _require_hashes(value: object, *, field: str, names: Sequence[str]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(names):
        raise SourceAdoptionPreparationError(f"{field} does not match the package contract")
    return {name: _require_sha256(value.get(name), field=f"{field}.{name}") for name in names}


def _validate_canonical_release_tree_descriptor(payload: bytes) -> dict[str, Any]:
    descriptor = _parse_canonical_json(payload, field="canonical application release descriptor")
    expected = {"schema", "status", "application", "files", "files_sha256"}
    if (
        set(descriptor) != expected
        or descriptor.get("schema") != "gold-trade-webapp-fi-canonical-release-tree-v1"
        or descriptor.get("status") != "prepared"
    ):
        raise SourceAdoptionPreparationError("canonical application release descriptor is unsupported")
    application = descriptor.get("application")
    if not isinstance(application, Mapping) or set(application) != {"release_sha", "git_tree"}:
        raise SourceAdoptionPreparationError("canonical application release descriptor application is invalid")
    release = application.get("release_sha")
    tree = application.get("git_tree")
    if not isinstance(release, str) or not RELEASE_RE.fullmatch(release):
        raise SourceAdoptionPreparationError("canonical application release descriptor release is invalid")
    if not isinstance(tree, str) or not COMMIT_RE.fullmatch(tree):
        raise SourceAdoptionPreparationError("canonical application release descriptor tree is invalid")
    raw_files = descriptor.get("files")
    if not isinstance(raw_files, list) or not raw_files or len(raw_files) > MAX_CANONICAL_RELEASE_FILES:
        raise SourceAdoptionPreparationError("canonical application release descriptor files are invalid")
    files: list[dict[str, Any]] = []
    prior_path = ""
    for item in raw_files:
        if not isinstance(item, Mapping) or set(item) != {"path", "mode", "sha256", "bytes"}:
            raise SourceAdoptionPreparationError("canonical application release descriptor file is invalid")
        path_value = item.get("path")
        if not isinstance(path_value, str):
            raise SourceAdoptionPreparationError("canonical application release descriptor path is invalid")
        path = _require_safe_release_path(path_value.encode("utf-8"))
        mode = item.get("mode")
        if mode not in {"100644", "100755"}:
            raise SourceAdoptionPreparationError("canonical application release descriptor mode is invalid")
        digest = _require_sha256(item.get("sha256"), field="canonical application release descriptor sha256")
        size = item.get("bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise SourceAdoptionPreparationError("canonical application release descriptor bytes is invalid")
        if prior_path and path <= prior_path:
            raise SourceAdoptionPreparationError("canonical application release descriptor paths are not strictly ordered")
        prior_path = path
        files.append({"path": path, "mode": mode, "sha256": digest, "bytes": size})
    if descriptor.get("files_sha256") != sha256_bytes(canonical_json_bytes(files)):
        raise SourceAdoptionPreparationError("canonical application release descriptor file hash is invalid")
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
        raise SourceAdoptionPreparationError("source-adoption contract is unsupported")
    if contract.get("source_site") != PACKAGE_DESTINATION_SITE or contract.get("destination_site") != SNAPSHOT_DESTINATION_SITE:
        raise SourceAdoptionPreparationError("source-adoption contract has an invalid site binding")
    _require_application(contract.get("application"), field="source-adoption contract application")
    _require_tooling(contract.get("tooling"), field="source-adoption contract tooling")
    _require_sha256(
        contract.get("canonical_release_tree_sha256"),
        field="source-adoption contract canonical_release_tree_sha256",
    )
    required_material = contract.get("external_material")
    expected_material = [
        "fresh_webapp_fi_bootstrap_age_identity",
        "separately_authorized_webapp_fi_source_signing_key_enrollment",
        "wa_ir_public_age_recipient",
        "ephemeral_version_bound_object_storage_control",
    ]
    if required_material != expected_material:
        raise SourceAdoptionPreparationError("source-adoption contract external material is unsupported")
    transport = contract.get("snapshot_transport")
    if transport != {
        "payload_path": "private_versioned_object_storage_age_only",
        "one_off_publication_only": True,
        "direct_webapp_fi_to_webapp_ir_transfer": False,
        "automatic_deletion": False,
    }:
        raise SourceAdoptionPreparationError("source-adoption contract transport is unsupported")
    forbidden = contract.get("forbidden_actions")
    if forbidden != [
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
        raise SourceAdoptionPreparationError("source-adoption contract forbidden actions are unsupported")
    return contract


def _validate_inner_manifest(payload: bytes) -> dict[str, Any]:
    manifest = _parse_canonical_json(payload, field="embedded source-adoption package manifest")
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
        raise SourceAdoptionPreparationError("embedded source-adoption package manifest is unsupported")
    if manifest.get("source_site") != PACKAGE_SOURCE_SITE or manifest.get("destination_site") != PACKAGE_DESTINATION_SITE:
        raise SourceAdoptionPreparationError("embedded source-adoption package manifest site binding is invalid")
    _require_package_id(manifest.get("package_id"), field="embedded source-adoption package manifest package_id")
    _require_application(manifest.get("application"), field="embedded source-adoption package manifest application")
    _require_tooling(manifest.get("tooling"), field="embedded source-adoption package manifest tooling")
    _require_hashes(manifest.get("files"), field="embedded source-adoption package manifest files", names=PACKAGE_PAYLOAD_FILES)
    _require_sha256(manifest.get("contract_sha256"), field="embedded source-adoption package manifest contract_sha256")
    return manifest


def _read_archive_members(path: Path) -> dict[str, bytes]:
    digest, size = sha256_file(path)
    if not 1 <= size <= MAX_ARCHIVE_BYTES:
        raise SourceAdoptionPreparationError("source-adoption archive has an unsafe size")
    del digest
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(path, mode="r:") as archive:
            entries = archive.getmembers()
            if {entry.name for entry in entries} != set(PACKAGE_FILES) or len(entries) != len(PACKAGE_FILES):
                raise SourceAdoptionPreparationError("source-adoption archive members do not match the package contract")
            for entry in entries:
                pure = PurePosixPath(entry.name)
                if (
                    pure.as_posix() != entry.name
                    or entry.name.startswith("/")
                    or ".." in pure.parts
                    or not entry.isfile()
                    or entry.issym()
                    or entry.islnk()
                    or entry.size < 1
                    or entry.size > MAX_PACKAGE_MEMBER_BYTES
                ):
                    raise SourceAdoptionPreparationError("source-adoption archive contains an unsafe member")
                handle = archive.extractfile(entry)
                if handle is None:
                    raise SourceAdoptionPreparationError("source-adoption archive member cannot be read")
                value = handle.read(entry.size + 1)
                if len(value) != entry.size:
                    raise SourceAdoptionPreparationError("source-adoption archive member size changed")
                members[entry.name] = value
    except (OSError, tarfile.TarError) as exc:
        raise SourceAdoptionPreparationError("source-adoption archive cannot be verified") from exc
    return members


def _read_private_json(path: Path, *, field: str) -> tuple[dict[str, Any], bytes]:
    path = _require_absolute(path, field=field)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SourceAdoptionPreparationError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or state.st_uid != 0
        or stat.S_IMODE(state.st_mode) & 0o077
        or state.st_size < 1
        or state.st_size > MAX_PACKAGE_MEMBER_BYTES
    ):
        raise SourceAdoptionPreparationError(f"{field} has unsafe ownership, mode, or size")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SourceAdoptionPreparationError(f"cannot read {field}") from exc
    return _parse_canonical_json(raw, field=field), raw


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
        raise SourceAdoptionPreparationError("source-adoption preparation receipt is unsupported")
    receipt_sha = _require_sha256(value.get("receipt_sha256"), field="source-adoption preparation receipt receipt_sha256")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if sha256_bytes(canonical_json_bytes(unsigned)) != receipt_sha:
        raise SourceAdoptionPreparationError("source-adoption preparation receipt hash is invalid")
    package_id = _require_package_id(value.get("package_id"), field="source-adoption preparation receipt package_id")
    package_directory = value.get("package_directory")
    if not isinstance(package_directory, str) or not package_directory.startswith("/"):
        raise SourceAdoptionPreparationError("source-adoption preparation receipt package_directory is invalid")
    if value.get("source_site") != PACKAGE_SOURCE_SITE or value.get("destination_site") != PACKAGE_DESTINATION_SITE:
        raise SourceAdoptionPreparationError("source-adoption preparation receipt site binding is invalid")
    application = _require_application(value.get("application"), field="source-adoption preparation receipt application")
    tooling = _require_tooling(value.get("tooling"), field="source-adoption preparation receipt tooling")
    archive = value.get("archive")
    if not isinstance(archive, Mapping) or set(archive) != {"name", "sha256", "bytes"} or archive.get("name") != PACKAGE_ARCHIVE_NAME:
        raise SourceAdoptionPreparationError("source-adoption preparation receipt archive is invalid")
    archive_sha = _require_sha256(archive.get("sha256"), field="source-adoption preparation receipt archive sha256")
    archive_bytes = _require_size(archive.get("bytes"), field="source-adoption preparation receipt archive bytes", maximum=MAX_ARCHIVE_BYTES)
    manifest = value.get("package_manifest")
    if not isinstance(manifest, Mapping) or set(manifest) != {"name", "sha256"} or manifest.get("name") != PACKAGE_MANIFEST_MEMBER:
        raise SourceAdoptionPreparationError("source-adoption preparation receipt manifest is invalid")
    manifest_sha = _require_sha256(manifest.get("sha256"), field="source-adoption preparation receipt manifest sha256")
    return {
        "package_id": package_id,
        "package_directory": package_directory,
        "application": application,
        "tooling": tooling,
        "archive_sha256": archive_sha,
        "archive_bytes": archive_bytes,
        "package_manifest_sha256": manifest_sha,
        "receipt_sha256": receipt_sha,
    }


def verify_prepared_source_adoption_package(
    *,
    package_directory: Path,
    preparation_receipt: Path,
    expected_control_commit: str,
    expected_application_release_sha: str,
) -> dict[str, Any]:
    """Verify a prepared package without installing or transporting it."""

    _require_root_execution()
    if not COMMIT_RE.fullmatch(expected_control_commit):
        raise SourceAdoptionPreparationError("expected_control_commit is invalid")
    if not RELEASE_RE.fullmatch(expected_application_release_sha):
        raise SourceAdoptionPreparationError("expected_application_release_sha is invalid")
    package = _require_root_directory(package_directory, field="source-adoption package directory", private=True)
    receipt_path = _require_absolute(preparation_receipt, field="source-adoption preparation receipt")
    if receipt_path != package / PREPARATION_RECEIPT_NAME:
        raise SourceAdoptionPreparationError("source-adoption preparation receipt path is not package-bound")
    receipt_value, receipt_raw = _read_private_json(receipt_path, field="source-adoption preparation receipt")
    receipt = _validate_preparation_receipt(receipt_value)
    if receipt["package_directory"] != str(package):
        raise SourceAdoptionPreparationError("source-adoption preparation receipt package directory is inconsistent")
    if receipt["tooling"]["control_commit"] != expected_control_commit:
        raise SourceAdoptionPreparationError("source-adoption preparation receipt control commit is unexpected")
    if receipt["application"]["release_sha"] != expected_application_release_sha:
        raise SourceAdoptionPreparationError("source-adoption preparation receipt application release is unexpected")
    if {item.name for item in package.iterdir()} != {PACKAGE_ARCHIVE_NAME, PREPARATION_RECEIPT_NAME}:
        raise SourceAdoptionPreparationError("source-adoption package directory contains unexpected entries")
    archive = package / PACKAGE_ARCHIVE_NAME
    actual_sha, actual_bytes = sha256_file(archive)
    if actual_sha != receipt["archive_sha256"] or actual_bytes != receipt["archive_bytes"]:
        raise SourceAdoptionPreparationError("source-adoption archive does not match its preparation receipt")
    members = _read_archive_members(archive)
    manifest_raw = members[PACKAGE_MANIFEST_MEMBER]
    manifest = _validate_inner_manifest(manifest_raw)
    if sha256_bytes(manifest_raw) != receipt["package_manifest_sha256"]:
        raise SourceAdoptionPreparationError("source-adoption package manifest does not match its preparation receipt")
    payload_hashes = {name: sha256_bytes(members[name]) for name in PACKAGE_PAYLOAD_FILES}
    if payload_hashes != manifest["files"]:
        raise SourceAdoptionPreparationError("source-adoption archive payload hashes do not match its manifest")
    contract_raw = members[CONTRACT_MEMBER]
    contract = _validate_contract(contract_raw)
    if sha256_bytes(contract_raw) != manifest["contract_sha256"]:
        raise SourceAdoptionPreparationError("source-adoption contract does not match its manifest")
    descriptor_raw = members[CANONICAL_RELEASE_TREE_MEMBER]
    descriptor = _validate_canonical_release_tree_descriptor(descriptor_raw)
    if sha256_bytes(descriptor_raw) != contract["canonical_release_tree_sha256"]:
        raise SourceAdoptionPreparationError("canonical application release descriptor does not match its contract")
    if (
        manifest["package_id"] != receipt["package_id"]
        or manifest["application"] != receipt["application"]
        or manifest["tooling"] != receipt["tooling"]
        or contract["application"] != receipt["application"]
        or contract["tooling"] != receipt["tooling"]
        or descriptor["application"]["release_sha"] != receipt["application"]["release_sha"]
    ):
        raise SourceAdoptionPreparationError("source-adoption package bindings are inconsistent")
    return {
        "status": "verified",
        "package_directory": str(package),
        "archive_path": str(archive),
        "archive_sha256": actual_sha,
        "archive_bytes": actual_bytes,
        "package_id": receipt["package_id"],
        "application": receipt["application"],
        "tooling": receipt["tooling"],
        "canonical_release_tree_sha256": sha256_bytes(descriptor_raw),
        "preparation_receipt_sha256": sha256_bytes(receipt_raw),
    }


def _require_root_only_file(path: Path, *, field: str, maximum_bytes: int) -> Path:
    path = _require_absolute(path, field=field)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SourceAdoptionPreparationError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or state.st_uid != 0
        or stat.S_IMODE(state.st_mode) & 0o077
        or not 1 <= state.st_size <= maximum_bytes
    ):
        raise SourceAdoptionPreparationError(f"{field} has unsafe ownership, mode, or size")
    return resolved


def _require_campaign_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not CAMPAIGN_ID_RE.fullmatch(value):
        raise SourceAdoptionPreparationError(f"{field} is invalid")
    return value


def _require_object_binding(
    *,
    object_key: object,
    version_id: object,
    ciphertext_sha256: object,
    ciphertext_bytes: object,
    plaintext_sha256: object,
    plaintext_bytes: object,
) -> dict[str, Any]:
    if not isinstance(object_key, str) or not OBJECT_KEY_RE.fullmatch(object_key):
        raise SourceAdoptionPreparationError("delivery object_key is invalid")
    if not isinstance(version_id, str) or not version_id or len(version_id) > 1024 or any(ord(item) < 0x20 for item in version_id):
        raise SourceAdoptionPreparationError("delivery version_id is invalid")
    return {
        "object_key": object_key,
        "version_id": version_id,
        "ciphertext_sha256": _require_sha256(ciphertext_sha256, field="delivery ciphertext_sha256"),
        "ciphertext_bytes": _require_size(ciphertext_bytes, field="delivery ciphertext_bytes", maximum=MAX_ARCHIVE_BYTES + 1024 * 1024),
        "plaintext_sha256": _require_sha256(plaintext_sha256, field="delivery plaintext_sha256"),
        "plaintext_bytes": _require_size(plaintext_bytes, field="delivery plaintext_bytes", maximum=MAX_ARCHIVE_BYTES),
    }


def _load_controller_signer(private_key_path: Path) -> tuple[Any, str]:
    """Load an already-enrolled controller key; this function never creates one."""

    private_key_path = _require_root_only_file(
        private_key_path,
        field="controller delivery-envelope signing private key",
        maximum_bytes=32,
    )
    raw = private_key_path.read_bytes()
    if len(raw) != 32:
        raise SourceAdoptionPreparationError("controller delivery-envelope signing private key must contain exactly 32 bytes")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise SourceAdoptionPreparationError("cryptography Ed25519 support is unavailable") from exc
    try:
        signer = Ed25519PrivateKey.from_private_bytes(raw)
        public = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    except ValueError as exc:
        raise SourceAdoptionPreparationError("controller delivery-envelope signing private key is invalid") from exc
    return signer, base64.b64encode(public).decode("ascii")


def sign_delivery_envelope(
    *,
    package_directory: Path,
    preparation_receipt: Path,
    expected_control_commit: str,
    expected_application_release_sha: str,
    campaign_id: str,
    fi_bootstrap_recipient: str,
    object_key: str,
    version_id: str,
    ciphertext_sha256: str,
    ciphertext_bytes: int,
    plaintext_sha256: str,
    plaintext_bytes: int,
    controller_signing_private_key: Path,
    destination: Path,
    apply: bool,
) -> dict[str, Any]:
    """Write a create-only controller-signed delivery envelope.

    This is deliberately separate from package preparation and from any S3
    action.  Callers must first complete a separately authorised Object
    Storage upload/read-back, then bind the exact returned VersionId here.
    The signing private key must already be enrolled by a separate operation.
    """

    _require_root_execution()
    verified = verify_prepared_source_adoption_package(
        package_directory=package_directory,
        preparation_receipt=preparation_receipt,
        expected_control_commit=expected_control_commit,
        expected_application_release_sha=expected_application_release_sha,
    )
    campaign = _require_campaign_id(campaign_id, field="campaign_id")
    if not isinstance(fi_bootstrap_recipient, str) or not AGE_RECIPIENT_RE.fullmatch(fi_bootstrap_recipient):
        raise SourceAdoptionPreparationError("fi_bootstrap_recipient is invalid")
    binding = _require_object_binding(
        object_key=object_key,
        version_id=version_id,
        ciphertext_sha256=ciphertext_sha256,
        ciphertext_bytes=ciphertext_bytes,
        plaintext_sha256=plaintext_sha256,
        plaintext_bytes=plaintext_bytes,
    )
    if binding["plaintext_sha256"] != verified["archive_sha256"] or binding["plaintext_bytes"] != verified["archive_bytes"]:
        raise SourceAdoptionPreparationError("delivery envelope plaintext binding is not the verified package archive")
    destination = _require_absolute(destination, field="delivery envelope destination")
    parent = _require_root_directory(destination.parent, field="delivery envelope destination parent", private=True)
    if destination.exists() or destination.is_symlink() or destination.parent != parent:
        raise SourceAdoptionPreparationError("delivery envelope destination must be a new child of a root-only directory")
    signer, controller_public_key_base64 = _load_controller_signer(controller_signing_private_key)
    if not apply:
        return {
            "status": "planned",
            "delivery_envelope_path": str(destination),
            "campaign_id": campaign,
            "package_id": verified["package_id"],
            "object": {"object_key": binding["object_key"], "version_id": binding["version_id"]},
            "controller_public_key_base64": controller_public_key_base64,
            "object_storage_action": False,
        }
    unsigned: dict[str, Any] = {
        "schema": DELIVERY_ENVELOPE_SCHEMA,
        "status": "issued",
        "campaign_id": campaign,
        "source_site": PACKAGE_SOURCE_SITE,
        "destination_site": PACKAGE_DESTINATION_SITE,
        "package_id": verified["package_id"],
        "application": verified["application"],
        "tooling": verified["tooling"],
        "canonical_release_tree_sha256": verified["canonical_release_tree_sha256"],
        "fi_bootstrap_recipient": fi_bootstrap_recipient,
        "object": binding,
        "controller_public_key_base64": controller_public_key_base64,
    }
    signature = signer.sign(DELIVERY_ENVELOPE_SIGNATURE_DOMAIN + canonical_json_bytes(unsigned))
    envelope = {
        **unsigned,
        "controller_signature": {"algorithm": "ed25519", "signature_base64": base64.b64encode(signature).decode("ascii")},
    }
    encoded = canonical_json_bytes(envelope) + b"\n"
    if b"https://" in encoded or b"presigned" in encoded.lower() or b'"url"' in encoded.lower():
        raise SourceAdoptionPreparationError("delivery envelope must not persist a URL")
    _write_new_private_file(destination, encoded)
    return {
        "status": "issued",
        "delivery_envelope_path": str(destination),
        "delivery_envelope_sha256": sha256_bytes(encoded),
        "campaign_id": campaign,
        "package_id": verified["package_id"],
        "object": {"object_key": binding["object_key"], "version_id": binding["version_id"]},
        "controller_public_key_base64": controller_public_key_base64,
    }


def build_contract(
    *,
    application_release_sha: str,
    expected_alembic_revision: str,
    control_commit: str,
    control_tree: str,
    canonical_release_tree_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": CONTRACT_SCHEMA,
        "status": "prepared",
        "source_site": PACKAGE_DESTINATION_SITE,
        "destination_site": SNAPSHOT_DESTINATION_SITE,
        "application": {
            "release_sha": application_release_sha,
            "expected_alembic_revision": expected_alembic_revision,
        },
        "tooling": {"control_commit": control_commit, "control_tree": control_tree},
        "canonical_release_tree_sha256": canonical_release_tree_sha256,
        "external_material": [
            "fresh_webapp_fi_bootstrap_age_identity",
            "separately_authorized_webapp_fi_source_signing_key_enrollment",
            "wa_ir_public_age_recipient",
            "ephemeral_version_bound_object_storage_control",
        ],
        "snapshot_transport": {
            "payload_path": "private_versioned_object_storage_age_only",
            "one_off_publication_only": True,
            "direct_webapp_fi_to_webapp_ir_transfer": False,
            "automatic_deletion": False,
        },
        "forbidden_actions": [
            "current",
            "service",
            "container_lifecycle",
            "volume",
            "application_data",
            "migration",
            "seed_restore",
            "failover",
            "full_matrix",
        ],
    }


def prepare_source_adoption_package(
    *,
    source_repository: Path,
    application_source_repository: Path,
    control_commit: str,
    application_release_sha: str,
    expected_alembic_revision: str,
    package_id: str,
    destination: Path,
    apply: bool,
) -> dict[str, Any]:
    """Create one detached package.  This function has no network side effect."""

    _require_root_execution()
    if not RELEASE_RE.fullmatch(application_release_sha):
        raise SourceAdoptionPreparationError("application_release_sha is invalid")
    if not ALEMBIC_RE.fullmatch(expected_alembic_revision):
        raise SourceAdoptionPreparationError("expected_alembic_revision is invalid")
    package_id = _require_package_id(package_id, field="package_id")
    repository, control_tree = _require_control_source(source_repository, control_commit)
    canonical_release_tree = build_canonical_release_tree_descriptor(
        application_source_repository=application_source_repository,
        application_release_sha=application_release_sha,
    )
    canonical_release_tree_raw = canonical_json_bytes(canonical_release_tree) + b"\n"
    payload_files = {
        relative: _source_file(repository, control_commit, relative)
        for relative in SOURCE_PAYLOAD_FILES
    }
    contract = build_contract(
        application_release_sha=application_release_sha,
        expected_alembic_revision=expected_alembic_revision,
        control_commit=control_commit,
        control_tree=control_tree,
        canonical_release_tree_sha256=sha256_bytes(canonical_release_tree_raw),
    )
    contract_raw = canonical_json_bytes(contract) + b"\n"
    _validate_contract(contract_raw)
    payload_files[CONTRACT_MEMBER] = contract_raw
    payload_files[CANONICAL_RELEASE_TREE_MEMBER] = canonical_release_tree_raw
    hashes = {name: sha256_bytes(payload) for name, payload in sorted(payload_files.items())}
    manifest: dict[str, Any] = {
        "schema": PACKAGE_SCHEMA,
        "status": "prepared",
        "package_id": package_id,
        "source_site": PACKAGE_SOURCE_SITE,
        "destination_site": PACKAGE_DESTINATION_SITE,
        "application": contract["application"],
        "tooling": contract["tooling"],
        "files": hashes,
        "contract_sha256": hashes[CONTRACT_MEMBER],
    }
    manifest_raw = canonical_json_bytes(manifest) + b"\n"
    _validate_inner_manifest(manifest_raw)
    files = {**payload_files, PACKAGE_MANIFEST_MEMBER: manifest_raw}
    destination = _require_absolute(destination, field="destination")
    parent = _require_root_directory(destination.parent, field="destination parent", private=True)
    if destination.exists() or destination.is_symlink() or destination.parent != parent:
        raise SourceAdoptionPreparationError("destination must be a new child of a root-only directory")
    if not apply:
        return {
            "schema": PREPARATION_RECEIPT_SCHEMA,
            "status": "planned",
            "package_directory": str(destination),
            "package_id": package_id,
            "application": contract["application"],
            "tooling": contract["tooling"],
            "network_action": False,
            "object_storage_action": False,
        }
    package = _require_new_package_directory(destination)
    archive = package / PACKAGE_ARCHIVE_NAME
    archive_sha, archive_bytes = _write_deterministic_archive(archive, files)
    _read_archive_members(archive)
    receipt: dict[str, Any] = {
        "schema": PREPARATION_RECEIPT_SCHEMA,
        "status": "prepared",
        "package_id": package_id,
        "package_directory": str(package),
        "source_site": PACKAGE_SOURCE_SITE,
        "destination_site": PACKAGE_DESTINATION_SITE,
        "application": contract["application"],
        "tooling": contract["tooling"],
        "archive": {"name": PACKAGE_ARCHIVE_NAME, "sha256": archive_sha, "bytes": archive_bytes},
        "package_manifest": {"name": PACKAGE_MANIFEST_MEMBER, "sha256": sha256_bytes(manifest_raw)},
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    receipt_path = package / PREPARATION_RECEIPT_NAME
    _write_new_private_file(receipt_path, canonical_json_bytes(receipt) + b"\n")
    return verify_prepared_source_adoption_package(
        package_directory=package,
        preparation_receipt=receipt_path,
        expected_control_commit=control_commit,
        expected_application_release_sha=application_release_sha,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    package = actions.add_parser("prepare-package")
    package.add_argument("--source-repository", type=Path, required=True)
    package.add_argument("--application-source-repository", type=Path, required=True)
    package.add_argument("--control-commit", required=True)
    package.add_argument("--application-release-sha", required=True)
    package.add_argument("--expected-alembic-revision", required=True)
    package.add_argument("--package-id", required=True)
    package.add_argument("--destination", type=Path, required=True)
    package.add_argument("--apply", action="store_true")
    envelope = actions.add_parser("sign-delivery-envelope")
    envelope.add_argument("--package-directory", type=Path, required=True)
    envelope.add_argument("--preparation-receipt", type=Path, required=True)
    envelope.add_argument("--expected-control-commit", required=True)
    envelope.add_argument("--expected-application-release-sha", required=True)
    envelope.add_argument("--campaign-id", required=True)
    envelope.add_argument("--fi-bootstrap-recipient", required=True)
    envelope.add_argument("--object-key", required=True)
    envelope.add_argument("--version-id", required=True)
    envelope.add_argument("--ciphertext-sha256", required=True)
    envelope.add_argument("--ciphertext-bytes", type=int, required=True)
    envelope.add_argument("--plaintext-sha256", required=True)
    envelope.add_argument("--plaintext-bytes", type=int, required=True)
    envelope.add_argument("--controller-signing-private-key", type=Path, required=True)
    envelope.add_argument("--destination", type=Path, required=True)
    envelope.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _require_root_execution()
        if args.action == "prepare-package":
            result = prepare_source_adoption_package(
                source_repository=args.source_repository,
                application_source_repository=args.application_source_repository,
                control_commit=args.control_commit,
                application_release_sha=args.application_release_sha,
                expected_alembic_revision=args.expected_alembic_revision,
                package_id=args.package_id,
                destination=args.destination,
                apply=args.apply,
            )
        else:
            result = sign_delivery_envelope(
                package_directory=args.package_directory,
                preparation_receipt=args.preparation_receipt,
                expected_control_commit=args.expected_control_commit,
                expected_application_release_sha=args.expected_application_release_sha,
                campaign_id=args.campaign_id,
                fi_bootstrap_recipient=args.fi_bootstrap_recipient,
                object_key=args.object_key,
                version_id=args.version_id,
                ciphertext_sha256=args.ciphertext_sha256,
                ciphertext_bytes=args.ciphertext_bytes,
                plaintext_sha256=args.plaintext_sha256,
                plaintext_bytes=args.plaintext_bytes,
                controller_signing_private_key=args.controller_signing_private_key,
                destination=args.destination,
                apply=args.apply,
            )
    except SourceAdoptionPreparationError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2
    print(canonical_json_bytes(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
