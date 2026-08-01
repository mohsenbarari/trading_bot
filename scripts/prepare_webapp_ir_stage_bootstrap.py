#!/usr/bin/env python3
"""Prepare a detached, local-only WA-IR artifact-stage consumer bootstrap.

WA-IR deliberately receives release material only from private, versioned
Object Storage.  Its first encrypted release bundle therefore cannot assume
that the regular artifact-stage consumer is already installed.  This helper
prepares the small, separately transportable consumer package needed to close
that bootstrap gap.  It does not contact Object Storage, SSH to a host, run
Docker, install files on WA-IR, or activate any service.

The resulting archive is intended to be one additional age-encrypted,
immutable Object Storage artifact.  A separately authorised generic remote
downloader must verify its exact encrypted object version and hash, decrypt it
with the existing WA-IR bootstrap identity, and extract it only to a fresh
root-only candidate before the included consumer can stage application
artifacts.
"""

from __future__ import annotations

import argparse
import base64
import binascii
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
from urllib.parse import urlparse


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SCHEMA = "gold-trade-wa-ir-stage-bootstrap-package-v1"
RECEIPT_SCHEMA = "gold-trade-wa-ir-stage-bootstrap-preparation-v1"
CONSUMER_CONFIG_SCHEMA = "gold-trade-wa-ir-artifact-stage-config-v4"
MAX_CONTROL_FILE_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
SITE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{20,128}$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")
PREFIX_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,127}$")

WA_IR_BOOTSTRAP_SOURCE_SITE = "webapp_fi"
WA_IR_BOOTSTRAP_DESTINATION_SITE = "webapp_ir"
WA_IR_CAMPAIGN_IDENTITY_ROOT = "/etc/trading-bot-three-site/campaigns"
WA_IR_BOOTSTRAP_IDENTITY_SUFFIX = "webapp-ir/bootstrap.agekey"

PACKAGE_ARCHIVE_NAME = "wa-ir-artifact-stage-consumer.tar"
PACKAGE_MANIFEST_MEMBER = "bootstrap-package.json"
PREPARATION_RECEIPT_NAME = "bootstrap-preparation-receipt.json"

SOURCE_SCRIPT_FILES = (
    "scripts/manage_webapp_ir_artifact_stage.py",
    "scripts/manage_webapp_ir_snapshot.py",
    "scripts/manage_webapp_ir_release_provenance.py",
    "scripts/prepare_webapp_ir_artifact_bundle.py",
    "scripts/verify_webapp_fi_source_provenance.py",
    "scripts/install_webapp_ir_static_assets.py",
    "core/standby_snapshot_capacity.py",
    "scripts/webapp_ir_image_archive_contract.py",
)
PAYLOAD_FILES = (
    *SOURCE_SCRIPT_FILES,
    "config/consumer.json",
)
PACKAGE_FILES = (*PAYLOAD_FILES, PACKAGE_MANIFEST_MEMBER)


class BootstrapPreparationError(RuntimeError):
    """The detached bootstrap package cannot be proven safe."""


def wa_ir_bootstrap_identity_file(campaign_id: object) -> str:
    """Return the only campaign-scoped WA-IR identity path accepted here.

    The final artifact stage deliberately reuses the fresh campaign identity
    already required for the WA-IR static receiver.  This avoids a second key
    while refusing the legacy, non-campaign 2c08 identity path.
    """

    if not isinstance(campaign_id, str) or not CAMPAIGN_ID_RE.fullmatch(campaign_id):
        raise BootstrapPreparationError("campaign ID is invalid for the WA-IR bootstrap age identity")
    path = PurePosixPath(WA_IR_CAMPAIGN_IDENTITY_ROOT) / campaign_id / WA_IR_BOOTSTRAP_IDENTITY_SUFFIX
    value = path.as_posix()
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BootstrapPreparationError("campaign WA-IR bootstrap age identity path is invalid")
    return value


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BootstrapPreparationError("JSON input contains duplicate keys")
        result[key] = value
    return result


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> tuple[str, int]:
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


def _require_absolute(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise BootstrapPreparationError(f"{field} must be an absolute path")
    return path


def _require_root_directory(path: Path, *, field: str, private: bool) -> Path:
    path = _require_absolute(path, field=field)
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        resolved_metadata = resolved.lstat()
    except OSError as exc:
        raise BootstrapPreparationError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(resolved_metadata.st_mode)
        or not stat.S_ISDIR(resolved_metadata.st_mode)
    ):
        raise BootstrapPreparationError(f"{field} must be one canonical non-symlink directory")
    forbidden = 0o077 if private else 0o022
    if resolved_metadata.st_uid != 0 or resolved_metadata.st_mode & forbidden:
        level = "root-private" if private else "root-owned and not group/other writable"
        raise BootstrapPreparationError(f"{field} must be {level}")
    return resolved


def _read_root_only_file(
    path: Path,
    *,
    field: str,
    maximum_bytes: int = MAX_CONTROL_FILE_BYTES,
) -> bytes:
    path = _require_absolute(path, field=field)
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BootstrapPreparationError(f"cannot inspect {field}") from exc
    if resolved != path or stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise BootstrapPreparationError(f"{field} must be one canonical regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BootstrapPreparationError(f"cannot safely open {field}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > maximum_bytes
            or before.st_mode & 0o077
        ):
            raise BootstrapPreparationError(f"{field} has unsafe ownership, mode, or size")
        chunks: list[bytes] = []
        remaining = before.st_size + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        result = b"".join(chunks)
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(result) != before.st_size or any(
            getattr(before, name) != getattr(after, name) for name in identity
        ):
            raise BootstrapPreparationError(f"{field} changed while being read")
        return result
    finally:
        os.close(descriptor)


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
        raise BootstrapPreparationError("cannot verify the exact control source repository") from exc
    return result.stdout


def _require_reported_git_directory(repository: Path, argument: str, field: str) -> Path:
    raw = _run_git(repository, ["rev-parse", "--path-format=absolute", argument]).strip()
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BootstrapPreparationError(f"{field} is not UTF-8") from exc
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise BootstrapPreparationError(f"{field} is malformed")
    return _require_root_directory(Path(value), field=field, private=False)


def _require_control_source(repository: Path, control_release_sha: str) -> tuple[Path, str]:
    if not COMMIT_RE.fullmatch(control_release_sha):
        raise BootstrapPreparationError("control_release_sha is invalid")
    repository = _require_root_directory(repository, field="control source repository", private=False)
    if _run_git(repository, ["rev-parse", "--is-inside-work-tree"]).strip() != b"true":
        raise BootstrapPreparationError("control source repository is not a Git worktree")
    _require_reported_git_directory(repository, "--git-dir", "control source Git directory")
    _require_reported_git_directory(repository, "--git-common-dir", "control source common Git directory")
    if _run_git(repository, ["status", "--porcelain=v1", "--untracked-files=all"]):
        raise BootstrapPreparationError("control source repository must be clean")
    head = _run_git(repository, ["rev-parse", "HEAD^{commit}"]).strip().decode("ascii")
    if head != control_release_sha:
        raise BootstrapPreparationError("control source HEAD does not match control_release_sha")
    commit = _run_git(repository, ["rev-parse", "--verify", control_release_sha + "^{commit}"]).strip().decode("ascii")
    if commit != control_release_sha:
        raise BootstrapPreparationError("control source lacks the requested control release")
    tree = _run_git(repository, ["rev-parse", control_release_sha + "^{tree}"]).strip().decode("ascii")
    if not COMMIT_RE.fullmatch(tree):
        raise BootstrapPreparationError("control source tree identity is invalid")
    return repository, tree


def _source_file(repository: Path, control_release_sha: str, relative: str) -> bytes:
    pure = PurePosixPath(relative)
    if pure.as_posix() != relative or relative.startswith("/") or ".." in pure.parts:
        raise BootstrapPreparationError("control source path is unsafe")
    payload = _run_git(repository, ["show", f"{control_release_sha}:{relative}"])
    if not payload or len(payload) > MAX_CONTROL_FILE_BYTES:
        raise BootstrapPreparationError("required control source file has an unsafe size")
    return payload


def _require_string(value: object, field: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise BootstrapPreparationError(f"{field} is invalid")
    return value


def _validate_consumer_config(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapPreparationError("consumer config is not valid strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise BootstrapPreparationError("consumer config must be a JSON object")
    expected = {
        "schema",
        "campaign_id",
        "endpoint",
        "region",
        "bucket",
        "prefix",
        "age_binary",
        "age_identity_file",
        "age_recipient",
        "workspace",
        "source_site",
        "source_signing_public_key_base64",
        "webapp_fi_source_attestation_public_key_base64",
        "webapp_fi_controller_authorization_public_key_base64",
        "maximum_artifact_bytes",
    }
    if set(value) != expected:
        raise BootstrapPreparationError("consumer config fields do not match the non-secret schema")
    if value.get("schema") != CONSUMER_CONFIG_SCHEMA:
        raise BootstrapPreparationError("consumer config schema is unsupported")
    endpoint = _require_string(value.get("endpoint"), "consumer config endpoint")
    region = _require_string(value.get("region"), "consumer config region", maximum=128)
    parsed_endpoint = urlparse(endpoint)
    expected_host = f"s3.{region}.arvanstorage.ir"
    try:
        has_port = parsed_endpoint.port is not None
    except ValueError as exc:
        raise BootstrapPreparationError(
            "consumer config endpoint must be the HTTPS Arvan S3 endpoint for its configured region"
        ) from exc
    if (
        parsed_endpoint.scheme != "https"
        or parsed_endpoint.hostname != expected_host
        or parsed_endpoint.path not in ("", "/")
        or parsed_endpoint.query
        or parsed_endpoint.fragment
        or parsed_endpoint.username
        or parsed_endpoint.password
        or has_port
    ):
        raise BootstrapPreparationError(
            "consumer config endpoint must be the HTTPS Arvan S3 endpoint for its configured region"
        )
    bucket = _require_string(value.get("bucket"), "consumer config bucket", maximum=63)
    if not BUCKET_RE.fullmatch(bucket):
        raise BootstrapPreparationError("consumer config bucket is invalid")
    prefix = _require_string(value.get("prefix"), "consumer config prefix", maximum=512).strip("/")
    if not prefix or any(not PREFIX_COMPONENT_RE.fullmatch(item) for item in prefix.split("/")):
        raise BootstrapPreparationError("consumer config prefix is invalid")
    for field in ("age_binary", "age_identity_file", "workspace"):
        path = _require_string(value.get(field), "consumer config " + field)
        if not path.startswith("/"):
            raise BootstrapPreparationError("consumer config " + field + " must be absolute")
    campaign_id = _require_string(value.get("campaign_id"), "consumer config campaign_id", maximum=128)
    if not CAMPAIGN_ID_RE.fullmatch(campaign_id):
        raise BootstrapPreparationError("consumer config campaign_id is invalid")
    site = _require_string(value.get("source_site"), "consumer config source_site", maximum=64)
    if site != WA_IR_BOOTSTRAP_SOURCE_SITE or not SITE_RE.fullmatch(site):
        raise BootstrapPreparationError("consumer config must pin source_site to webapp_fi")
    if value.get("age_identity_file") != wa_ir_bootstrap_identity_file(campaign_id):
        raise BootstrapPreparationError("consumer config must pin the campaign WA-IR bootstrap age identity path")
    age_recipient = _require_string(value.get("age_recipient"), "consumer config age_recipient", maximum=256)
    if not AGE_RECIPIENT_RE.fullmatch(age_recipient):
        raise BootstrapPreparationError("consumer config age_recipient is invalid")
    encoded_key = _require_string(
        value.get("source_signing_public_key_base64"),
        "consumer config source_signing_public_key_base64",
        maximum=128,
    )
    try:
        public_key = base64.b64decode(encoded_key, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BootstrapPreparationError("consumer config source signing key is invalid") from exc
    if len(public_key) != 32:
        raise BootstrapPreparationError("consumer config source signing key has an unsafe length")
    fi_encoded_key = _require_string(
        value.get("webapp_fi_source_attestation_public_key_base64"),
        "consumer config WebApp-FI source attestation key",
        maximum=128,
    )
    try:
        fi_public_key = base64.b64decode(fi_encoded_key, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BootstrapPreparationError("consumer config WebApp-FI source attestation key is invalid") from exc
    if len(fi_public_key) != 32:
        raise BootstrapPreparationError("consumer config WebApp-FI source attestation key has an unsafe length")
    controller_encoded_key = _require_string(
        value.get("webapp_fi_controller_authorization_public_key_base64"),
        "consumer config WebApp-FI controller authorization key",
        maximum=128,
    )
    try:
        controller_public_key = base64.b64decode(controller_encoded_key, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BootstrapPreparationError("consumer config WebApp-FI controller authorization key is invalid") from exc
    if len(controller_public_key) != 32:
        raise BootstrapPreparationError(
            "consumer config WebApp-FI controller authorization key has an unsafe length"
        )
    maximum = value.get("maximum_artifact_bytes")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 100 * 1024 * 1024 * 1024:
        raise BootstrapPreparationError("consumer config maximum_artifact_bytes is invalid")
    return value


def _require_new_package_directory(destination: Path) -> Path:
    destination = _require_absolute(destination, field="destination")
    if destination.name in ("", ".", ".."):
        raise BootstrapPreparationError("destination name is invalid")
    parent = _require_root_directory(destination.parent, field="destination parent", private=True)
    if destination.exists() or destination.is_symlink():
        raise BootstrapPreparationError("destination must not already exist")
    try:
        destination.mkdir(mode=0o700)
        os.chmod(destination, 0o700)
    except OSError as exc:
        raise BootstrapPreparationError("cannot create a new bootstrap package directory") from exc
    try:
        resolved = destination.resolve(strict=True)
        metadata = destination.lstat()
    except OSError as exc:
        raise BootstrapPreparationError("cannot inspect the new bootstrap package directory") from exc
    if resolved.parent != parent or resolved != destination or not stat.S_ISDIR(metadata.st_mode):
        raise BootstrapPreparationError("new bootstrap package directory is unsafe")
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise BootstrapPreparationError("new bootstrap package directory has unsafe ownership or mode")
    return resolved


def _write_new_private_file(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise BootstrapPreparationError(f"cannot create bootstrap package file: {path.name}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        # Preserve a partial new package for forensic inspection; retries use a new path.
        raise


def _write_deterministic_archive(path: Path, files: Mapping[str, bytes]) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise BootstrapPreparationError("cannot create bootstrap archive") from exc
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
        # Do not erase failed evidence from the fresh package directory.
        raise
    digest, size = _sha256_file(path)
    if not 1 <= size <= MAX_ARCHIVE_BYTES:
        raise BootstrapPreparationError("bootstrap archive has an unsafe size")
    return digest, size


def _parse_canonical_json(payload: bytes, *, field: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapPreparationError(f"{field} is not valid strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise BootstrapPreparationError(f"{field} must be a JSON object")
    if payload != _canonical_json_bytes(value) + b"\n":
        raise BootstrapPreparationError(f"{field} must use canonical JSON")
    return value


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise BootstrapPreparationError(f"{field} is invalid")
    return value


def _require_positive_size(value: object, *, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise BootstrapPreparationError(f"{field} is invalid")
    return value


def _require_control(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"commit", "tree"}:
        raise BootstrapPreparationError(f"{field} is invalid")
    commit = value.get("commit")
    tree = value.get("tree")
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise BootstrapPreparationError(f"{field}.commit is invalid")
    if not isinstance(tree, str) or not COMMIT_RE.fullmatch(tree):
        raise BootstrapPreparationError(f"{field}.tree is invalid")
    return {"commit": commit, "tree": tree}


def _require_file_hashes(value: object, *, field: str, names: Sequence[str]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(names):
        raise BootstrapPreparationError(f"{field} does not match the package contract")
    result: dict[str, str] = {}
    for name in names:
        result[name] = _require_sha256(value.get(name), field=f"{field}.{name}")
    return result


def _require_archive_descriptor(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"name", "sha256", "bytes"}:
        raise BootstrapPreparationError(f"{field} is invalid")
    if value.get("name") != PACKAGE_ARCHIVE_NAME:
        raise BootstrapPreparationError(f"{field}.name is invalid")
    return {
        "name": PACKAGE_ARCHIVE_NAME,
        "sha256": _require_sha256(value.get("sha256"), field=f"{field}.sha256"),
        "bytes": _require_positive_size(value.get("bytes"), field=f"{field}.bytes", maximum=MAX_ARCHIVE_BYTES),
    }


def _require_package_manifest_descriptor(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"name", "sha256"}:
        raise BootstrapPreparationError(f"{field} is invalid")
    if value.get("name") != PACKAGE_MANIFEST_MEMBER:
        raise BootstrapPreparationError(f"{field}.name is invalid")
    return {
        "name": PACKAGE_MANIFEST_MEMBER,
        "sha256": _require_sha256(value.get("sha256"), field=f"{field}.sha256"),
    }


def _validate_inner_package_manifest(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BootstrapPreparationError("embedded bootstrap package manifest must be an object")
    manifest = dict(value)
    expected = {"schema", "status", "control", "files", "consumer_config_sha256"}
    if set(manifest) != expected or manifest.get("schema") != PACKAGE_SCHEMA or manifest.get("status") != "prepared":
        raise BootstrapPreparationError("embedded bootstrap package manifest is unsupported")
    control = _require_control(manifest.get("control"), field="embedded bootstrap package manifest control")
    hashes = _require_file_hashes(manifest.get("files"), field="embedded bootstrap package manifest files", names=PAYLOAD_FILES)
    config_sha256 = _require_sha256(
        manifest.get("consumer_config_sha256"),
        field="embedded bootstrap package manifest consumer_config_sha256",
    )
    if config_sha256 != hashes["config/consumer.json"]:
        raise BootstrapPreparationError("embedded bootstrap package manifest consumer config hash is inconsistent")
    return {
        "schema": PACKAGE_SCHEMA,
        "status": "prepared",
        "control": control,
        "files": hashes,
        "consumer_config_sha256": config_sha256,
    }


def _validate_preparation_receipt(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BootstrapPreparationError("bootstrap preparation receipt must be an object")
    receipt = dict(value)
    expected = {
        "schema",
        "status",
        "package_directory",
        "control_commit",
        "control_tree",
        "bootstrap_archive",
        "package_manifest",
        "consumer_config_sha256",
        "receipt_sha256",
    }
    if set(receipt) != expected or receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("status") != "prepared":
        raise BootstrapPreparationError("bootstrap preparation receipt is unsupported")
    package_directory = receipt.get("package_directory")
    if not isinstance(package_directory, str) or not package_directory.startswith("/"):
        raise BootstrapPreparationError("bootstrap preparation receipt package_directory is invalid")
    control_commit = receipt.get("control_commit")
    control_tree = receipt.get("control_tree")
    if not isinstance(control_commit, str) or not COMMIT_RE.fullmatch(control_commit):
        raise BootstrapPreparationError("bootstrap preparation receipt control_commit is invalid")
    if not isinstance(control_tree, str) or not COMMIT_RE.fullmatch(control_tree):
        raise BootstrapPreparationError("bootstrap preparation receipt control_tree is invalid")
    archive = _require_archive_descriptor(receipt.get("bootstrap_archive"), field="bootstrap preparation receipt bootstrap_archive")
    package_manifest = _require_package_manifest_descriptor(
        receipt.get("package_manifest"), field="bootstrap preparation receipt package_manifest"
    )
    config_sha256 = _require_sha256(
        receipt.get("consumer_config_sha256"), field="bootstrap preparation receipt consumer_config_sha256"
    )
    receipt_sha256 = _require_sha256(receipt.get("receipt_sha256"), field="bootstrap preparation receipt receipt_sha256")
    unsigned = {key: item for key, item in receipt.items() if key != "receipt_sha256"}
    if _sha256_bytes(_canonical_json_bytes(unsigned)) != receipt_sha256:
        raise BootstrapPreparationError("bootstrap preparation receipt hash is invalid")
    return {
        "schema": RECEIPT_SCHEMA,
        "status": "prepared",
        "package_directory": package_directory,
        "control_commit": control_commit,
        "control_tree": control_tree,
        "bootstrap_archive": archive,
        "package_manifest": package_manifest,
        "consumer_config_sha256": config_sha256,
        "receipt_sha256": receipt_sha256,
    }


def _read_archive_members(payload: bytes) -> dict[str, bytes]:
    if not 1 <= len(payload) <= MAX_ARCHIVE_BYTES:
        raise BootstrapPreparationError("bootstrap archive has an unsafe size")
    result: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            for entry in archive:
                pure = PurePosixPath(entry.name)
                if (
                    not entry.name
                    or entry.name.startswith("/")
                    or "\\" in entry.name
                    or any(part in ("", ".", "..") for part in pure.parts)
                    or not entry.isfile()
                    or entry.linkname
                    or entry.mode != 0o600
                    or entry.uid != 0
                    or entry.gid != 0
                    or entry.mtime != 0
                    or entry.size < 1
                    or entry.size > MAX_CONTROL_FILE_BYTES
                    or entry.name in result
                ):
                    raise BootstrapPreparationError("bootstrap archive has an unsafe entry")
                source = archive.extractfile(entry)
                if source is None:
                    raise BootstrapPreparationError("bootstrap archive entry cannot be read")
                entry_payload = source.read(entry.size + 1)
                if len(entry_payload) != entry.size:
                    raise BootstrapPreparationError("bootstrap archive entry has an unsafe size")
                result[entry.name] = entry_payload
    except (OSError, tarfile.TarError) as exc:
        raise BootstrapPreparationError("bootstrap archive cannot be safely verified") from exc
    return result


def _verify_archive(path: Path, expected: Mapping[str, str]) -> None:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise BootstrapPreparationError("bootstrap archive cannot be safely verified") from exc
    observed = {name: _sha256_bytes(value) for name, value in _read_archive_members(payload).items()}
    if observed != dict(expected):
        raise BootstrapPreparationError("bootstrap archive does not match expected file hashes")


def verify_prepared_bootstrap_package(
    *,
    package_directory: Path,
    preparation_receipt: Path,
    expected_control_release_sha: str | None = None,
) -> dict[str, Any]:
    """Verify one local bootstrap preparation before it may reach Object Storage."""

    package = _require_root_directory(package_directory, field="bootstrap package directory", private=True)
    expected_receipt_path = package / PREPARATION_RECEIPT_NAME
    if preparation_receipt != expected_receipt_path:
        raise BootstrapPreparationError("bootstrap preparation receipt must be the canonical package receipt")
    receipt_raw = _read_root_only_file(preparation_receipt, field="bootstrap preparation receipt")
    receipt = _validate_preparation_receipt(
        _parse_canonical_json(receipt_raw, field="bootstrap preparation receipt")
    )
    if receipt["package_directory"] != str(package):
        raise BootstrapPreparationError("bootstrap preparation receipt package_directory does not match")
    if expected_control_release_sha is not None:
        if not COMMIT_RE.fullmatch(expected_control_release_sha) or receipt["control_commit"] != expected_control_release_sha:
            raise BootstrapPreparationError("bootstrap preparation receipt control commit does not match")

    archive_path = package / PACKAGE_ARCHIVE_NAME
    archive_raw = _read_root_only_file(
        archive_path,
        field="bootstrap archive",
        maximum_bytes=MAX_ARCHIVE_BYTES,
    )
    archive_sha256 = _sha256_bytes(archive_raw)
    archive_bytes = len(archive_raw)
    if archive_sha256 != receipt["bootstrap_archive"]["sha256"] or archive_bytes != receipt["bootstrap_archive"]["bytes"]:
        raise BootstrapPreparationError("bootstrap archive does not match the preparation receipt")

    members = _read_archive_members(archive_raw)
    if tuple(members) != tuple(sorted(PACKAGE_FILES)):
        raise BootstrapPreparationError("bootstrap archive member schema is unsupported")
    inner_raw = members[PACKAGE_MANIFEST_MEMBER]
    inner_manifest = _validate_inner_package_manifest(
        _parse_canonical_json(inner_raw, field="embedded bootstrap package manifest")
    )
    if _sha256_bytes(inner_raw) != receipt["package_manifest"]["sha256"]:
        raise BootstrapPreparationError("embedded bootstrap package manifest hash does not match")
    payload_hashes = {name: _sha256_bytes(members[name]) for name in PAYLOAD_FILES}
    if payload_hashes != inner_manifest["files"]:
        raise BootstrapPreparationError("bootstrap archive payload hashes do not match the embedded manifest")
    if (
        inner_manifest["control"]["commit"] != receipt["control_commit"]
        or inner_manifest["control"]["tree"] != receipt["control_tree"]
        or inner_manifest["consumer_config_sha256"] != receipt["consumer_config_sha256"]
        or payload_hashes["config/consumer.json"] != receipt["consumer_config_sha256"]
    ):
        raise BootstrapPreparationError("bootstrap package control or consumer config binding is inconsistent")
    consumer_config = _validate_consumer_config(members["config/consumer.json"])
    return {
        "package_directory": str(package),
        "preparation_receipt": str(preparation_receipt),
        "archive_path": str(archive_path),
        "archive_sha256": archive_sha256,
        "archive_bytes": archive_bytes,
        "control_commit": receipt["control_commit"],
        "control_tree": receipt["control_tree"],
        "package_manifest_sha256": receipt["package_manifest"]["sha256"],
        "consumer_config_sha256": receipt["consumer_config_sha256"],
        "preparation_receipt_sha256": _sha256_bytes(receipt_raw),
        "receipt_sha256": receipt["receipt_sha256"],
        "consumer_config": consumer_config,
    }


def prepare_bootstrap_package(
    *,
    source_repository: Path,
    control_release_sha: str,
    consumer_config: Path,
    destination: Path,
) -> dict[str, Any]:
    """Prepare one detached package; it has no network or host-side effects."""

    repository, control_tree = _require_control_source(source_repository, control_release_sha)
    config_bytes = _read_root_only_file(consumer_config, field="consumer config")
    _validate_consumer_config(config_bytes)
    payload_files = {
        relative: _source_file(repository, control_release_sha, relative)
        for relative in SOURCE_SCRIPT_FILES
    }
    payload_files["config/consumer.json"] = config_bytes
    hashes = {name: _sha256_bytes(payload) for name, payload in sorted(payload_files.items())}
    embedded_manifest: dict[str, Any] = {
        "schema": PACKAGE_SCHEMA,
        "status": "prepared",
        "control": {"commit": control_release_sha, "tree": control_tree},
        "files": hashes,
        "consumer_config_sha256": hashes["config/consumer.json"],
    }
    embedded_manifest_bytes = _canonical_json_bytes(embedded_manifest) + b"\n"
    files = {**payload_files, PACKAGE_MANIFEST_MEMBER: embedded_manifest_bytes}
    package = _require_new_package_directory(destination)
    archive = package / PACKAGE_ARCHIVE_NAME
    archive_sha256, archive_bytes = _write_deterministic_archive(archive, files)
    _verify_archive(archive, {name: _sha256_bytes(payload) for name, payload in sorted(files.items())})
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "status": "prepared",
        "package_directory": str(package),
        "control_commit": control_release_sha,
        "control_tree": control_tree,
        "bootstrap_archive": {
            "name": archive.name,
            "sha256": archive_sha256,
            "bytes": archive_bytes,
        },
        "package_manifest": {
            "name": PACKAGE_MANIFEST_MEMBER,
            "sha256": _sha256_bytes(embedded_manifest_bytes),
        },
        "consumer_config_sha256": hashes["config/consumer.json"],
    }
    receipt["receipt_sha256"] = _sha256_bytes(_canonical_json_bytes(receipt))
    receipt_path = package / PREPARATION_RECEIPT_NAME
    _write_new_private_file(receipt_path, _canonical_json_bytes(receipt) + b"\n")
    verify_prepared_bootstrap_package(
        package_directory=package,
        preparation_receipt=receipt_path,
        expected_control_release_sha=control_release_sha,
    )
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repository", type=Path, required=True)
    parser.add_argument("--control-release-sha", required=True)
    parser.add_argument("--consumer-config", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = prepare_bootstrap_package(
            source_repository=arguments.source_repository,
            control_release_sha=arguments.control_release_sha,
            consumer_config=arguments.consumer_config,
            destination=arguments.destination,
        )
    except BootstrapPreparationError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}), file=sys.stderr)
        return 1
    print(_canonical_json_bytes(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
