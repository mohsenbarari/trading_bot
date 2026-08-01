#!/usr/bin/env python3
"""Render, but never execute, the first verified WA-IR bootstrap receive command.

The initial WA-IR artifact-stage consumer is deliberately received through one
private, versioned Object Storage object.  This helper is controller-local:
it validates root-only bootstrap metadata and emits exactly one safely quoted
SSH control command.  It does not invoke SSH, Object Storage, Docker, curl,
age, or tar itself.

The short-lived presigned URL is the final transient remote argument.  It is
not embedded in the generated Python payload and the remote receiver writes no
URL to its persistent receipt.  A just-published bootstrap receipt can also be
read directly from stdin, so its URL need not be written to a durable file.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import sys
import tarfile
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, quote, urlparse


REMOTE_HOST = "root@95.38.164.29"
SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes")
BOOTSTRAP_PUBLISH_RECEIPT_SCHEMA = "gold-trade-wa-ir-artifact-stage-bootstrap-publish-receipt-v1"
PREPARATION_RECEIPT_SCHEMA = "gold-trade-wa-ir-stage-bootstrap-preparation-v1"
PACKAGE_MANIFEST_SCHEMA = "gold-trade-wa-ir-stage-bootstrap-package-v1"
CONSUMER_CONFIG_SCHEMA = "gold-trade-wa-ir-artifact-stage-config-v3"
RECEIVE_RECEIPT_SCHEMA = "gold-trade-wa-ir-stage-bootstrap-receipt-v1"
TRANSPORT_SCHEMA = "gold-trade-wa-ir-artifact-stage-v1"
OBJECT_ENCRYPTION = "age-v1"

# This is pinned by the b3368af4 bootstrap preparer/publisher pair.  The
# publish receipt does not carry recipient metadata, so successful decryption
# with this exact root-only identity plus the independently verified package
# bindings is the receiver-side proof.  It is not a pre-decryption recipient
# metadata claim.
WA_IR_BOOTSTRAP_IDENTITY_FILE = "/etc/trading-bot-three-site/wa-ir/artifact-stage-2c08.agekey"
PACKAGE_ARCHIVE_NAME = "wa-ir-artifact-stage-consumer.tar"
PACKAGE_MANIFEST_MEMBER = "bootstrap-package.json"
PREPARATION_RECEIPT_NAME = "bootstrap-preparation-receipt.json"
PAYLOAD_MEMBERS = (
    "scripts/manage_webapp_ir_artifact_stage.py",
    "scripts/manage_webapp_ir_snapshot.py",
    "scripts/manage_webapp_ir_release_provenance.py",
    "core/standby_snapshot_capacity.py",
    "scripts/webapp_ir_image_archive_contract.py",
    "config/consumer.json",
)

MAX_CONTROL_FILE_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_CIPHERTEXT_BYTES = MAX_ARCHIVE_BYTES + 2 * 1024 * 1024
MAX_URL_BYTES = 8192
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")
PREFIX_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,127}$")
INSTALLER_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")


class BootstrapReceiveRenderError(RuntimeError):
    """The controller-side bootstrap evidence cannot be rendered safely."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BootstrapReceiveRenderError("JSON input contains duplicate keys")
        result[key] = value
    return result


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_json(payload: bytes, *, field: str, canonical: bool) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapReceiveRenderError(f"{field} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise BootstrapReceiveRenderError(f"{field} must be a JSON object")
    if canonical and payload != _canonical_json_bytes(value) + b"\n":
        raise BootstrapReceiveRenderError(f"{field} must use canonical JSON")
    return value


def _require_absolute_path(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024 or "\x00" in value:
        raise BootstrapReceiveRenderError(f"{field} is invalid")
    pure = PurePosixPath(value)
    if (
        not pure.is_absolute()
        or len(pure.parts) < 2
        or pure.as_posix() != value
        or any(part in ("", ".", "..") for part in pure.parts)
    ):
        raise BootstrapReceiveRenderError(f"{field} must be one canonical absolute path")
    return value


def _require_installer_compatible_path(value: object, *, field: str) -> str:
    path = _require_absolute_path(value, field=field)
    if not INSTALLER_PATH_RE.fullmatch(path):
        raise BootstrapReceiveRenderError(f"{field} is incompatible with the provenance installer")
    return path


def _read_root_only_file(path: Path, *, field: str, maximum_bytes: int = MAX_CONTROL_FILE_BYTES) -> bytes:
    if not path.is_absolute():
        raise BootstrapReceiveRenderError(f"{field} path must be absolute")
    try:
        before_lstat = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BootstrapReceiveRenderError(f"cannot inspect {field}") from exc
    if resolved != path or stat.S_ISLNK(before_lstat.st_mode) or not stat.S_ISREG(before_lstat.st_mode):
        raise BootstrapReceiveRenderError(f"{field} must be one canonical regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BootstrapReceiveRenderError(f"cannot securely open {field}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum_bytes
            or before.st_mode & 0o077
            or before.st_dev != before_lstat.st_dev
            or before.st_ino != before_lstat.st_ino
        ):
            raise BootstrapReceiveRenderError(f"{field} has unsafe ownership, mode, or size")
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
        if len(result) != before.st_size or any(getattr(before, name) != getattr(after, name) for name in identity):
            raise BootstrapReceiveRenderError(f"{field} changed while being read")
        return result
    finally:
        os.close(descriptor)


def _read_publish_receipt_stdin() -> bytes:
    """Read one bounded, transient publish receipt without creating a file."""

    stream = getattr(sys.stdin, "buffer", sys.stdin)
    try:
        payload = stream.read(MAX_CONTROL_FILE_BYTES + 1)
    except OSError as exc:
        raise BootstrapReceiveRenderError("cannot read bootstrap publish receipt from stdin") from exc
    if not isinstance(payload, bytes):
        raise BootstrapReceiveRenderError("bootstrap publish receipt stdin must be binary")
    if not payload:
        raise BootstrapReceiveRenderError("bootstrap publish receipt stdin is empty")
    if len(payload) > MAX_CONTROL_FILE_BYTES:
        raise BootstrapReceiveRenderError("bootstrap publish receipt stdin exceeds the fixed size bound")
    return payload


def _require_root_private_directory(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise BootstrapReceiveRenderError(f"{field} path must be absolute")
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        after = resolved.lstat()
    except OSError as exc:
        raise BootstrapReceiveRenderError(f"cannot inspect {field}") from exc
    if resolved != path or stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(after.st_mode):
        raise BootstrapReceiveRenderError(f"{field} must be one canonical directory")
    if after.st_uid != 0 or after.st_mode & 0o077:
        raise BootstrapReceiveRenderError(f"{field} must be root-private")
    return resolved


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise BootstrapReceiveRenderError(f"{field} is invalid")
    return value


def _require_positive_size(value: object, *, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise BootstrapReceiveRenderError(f"{field} is invalid")
    return value


def _require_text(value: object, *, field: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise BootstrapReceiveRenderError(f"{field} is invalid")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise BootstrapReceiveRenderError(f"{field} contains control characters")
    return value


def _require_control(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"commit", "tree"}:
        raise BootstrapReceiveRenderError(f"{field} is invalid")
    commit = value.get("commit")
    tree = value.get("tree")
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise BootstrapReceiveRenderError(f"{field}.commit is invalid")
    if not isinstance(tree, str) or not COMMIT_RE.fullmatch(tree):
        raise BootstrapReceiveRenderError(f"{field}.tree is invalid")
    return {"commit": commit, "tree": tree}


def _require_payload_hashes(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(PAYLOAD_MEMBERS):
        raise BootstrapReceiveRenderError(f"{field} does not match the package contract")
    return {name: _require_sha256(value.get(name), field=f"{field}.{name}") for name in PAYLOAD_MEMBERS}


def _validate_package_manifest(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BootstrapReceiveRenderError("bootstrap package manifest must be an object")
    manifest = dict(value)
    expected = {"schema", "status", "control", "files", "consumer_config_sha256"}
    if set(manifest) != expected or manifest.get("schema") != PACKAGE_MANIFEST_SCHEMA or manifest.get("status") != "prepared":
        raise BootstrapReceiveRenderError("bootstrap package manifest is unsupported")
    control = _require_control(manifest.get("control"), field="bootstrap package manifest control")
    files = _require_payload_hashes(manifest.get("files"), field="bootstrap package manifest files")
    config_sha = _require_sha256(manifest.get("consumer_config_sha256"), field="bootstrap package manifest consumer_config_sha256")
    if config_sha != files["config/consumer.json"]:
        raise BootstrapReceiveRenderError("bootstrap package manifest consumer config hash is inconsistent")
    return {"control": control, "files": files, "consumer_config_sha256": config_sha}


def _validate_preparation_receipt(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BootstrapReceiveRenderError("bootstrap preparation receipt must be an object")
    receipt = dict(value)
    expected = {
        "schema", "status", "package_directory", "control_commit", "control_tree", "bootstrap_archive",
        "package_manifest", "consumer_config_sha256", "receipt_sha256",
    }
    if set(receipt) != expected or receipt.get("schema") != PREPARATION_RECEIPT_SCHEMA or receipt.get("status") != "prepared":
        raise BootstrapReceiveRenderError("bootstrap preparation receipt is unsupported")
    package_directory = _require_absolute_path(
        receipt.get("package_directory"), field="bootstrap preparation receipt package_directory"
    )
    control_commit = receipt.get("control_commit")
    control_tree = receipt.get("control_tree")
    if not isinstance(control_commit, str) or not COMMIT_RE.fullmatch(control_commit):
        raise BootstrapReceiveRenderError("bootstrap preparation receipt control_commit is invalid")
    if not isinstance(control_tree, str) or not COMMIT_RE.fullmatch(control_tree):
        raise BootstrapReceiveRenderError("bootstrap preparation receipt control_tree is invalid")
    archive_value = receipt.get("bootstrap_archive")
    if not isinstance(archive_value, Mapping) or set(archive_value) != {"name", "sha256", "bytes"}:
        raise BootstrapReceiveRenderError("bootstrap preparation receipt bootstrap_archive is invalid")
    if archive_value.get("name") != PACKAGE_ARCHIVE_NAME:
        raise BootstrapReceiveRenderError("bootstrap preparation receipt archive name is invalid")
    archive = {
        "name": PACKAGE_ARCHIVE_NAME,
        "sha256": _require_sha256(archive_value.get("sha256"), field="bootstrap preparation receipt archive sha256"),
        "bytes": _require_positive_size(archive_value.get("bytes"), field="bootstrap preparation receipt archive bytes", maximum=MAX_ARCHIVE_BYTES),
    }
    manifest_value = receipt.get("package_manifest")
    if not isinstance(manifest_value, Mapping) or set(manifest_value) != {"name", "sha256"}:
        raise BootstrapReceiveRenderError("bootstrap preparation receipt package_manifest is invalid")
    if manifest_value.get("name") != PACKAGE_MANIFEST_MEMBER:
        raise BootstrapReceiveRenderError("bootstrap preparation receipt package manifest name is invalid")
    manifest_sha = _require_sha256(manifest_value.get("sha256"), field="bootstrap preparation receipt package manifest sha256")
    config_sha = _require_sha256(receipt.get("consumer_config_sha256"), field="bootstrap preparation receipt consumer config sha256")
    receipt_sha = _require_sha256(receipt.get("receipt_sha256"), field="bootstrap preparation receipt receipt sha256")
    unsigned = {key: item for key, item in receipt.items() if key != "receipt_sha256"}
    if _sha256_bytes(_canonical_json_bytes(unsigned)) != receipt_sha:
        raise BootstrapReceiveRenderError("bootstrap preparation receipt self-hash is invalid")
    return {
        "package_directory": package_directory,
        "control_commit": control_commit,
        "control_tree": control_tree,
        "archive": archive,
        "manifest_sha256": manifest_sha,
        "consumer_config_sha256": config_sha,
        "receipt_sha256": receipt_sha,
    }


def _validate_consumer_config(payload: bytes) -> dict[str, Any]:
    value = _parse_json(payload, field="consumer config", canonical=False)
    expected = {
        "schema", "endpoint", "region", "bucket", "prefix", "age_binary", "age_identity_file", "workspace",
        "source_site", "source_signing_public_key_base64", "webapp_fi_source_attestation_public_key_base64",
        "webapp_fi_controller_authorization_public_key_base64", "maximum_artifact_bytes",
    }
    if set(value) != expected or value.get("schema") != CONSUMER_CONFIG_SCHEMA:
        raise BootstrapReceiveRenderError("consumer config schema is unsupported")
    endpoint = _require_text(value.get("endpoint"), field="consumer config endpoint")
    region = _require_text(value.get("region"), field="consumer config region", maximum=128)
    parsed = urlparse(endpoint)
    try:
        has_port = parsed.port is not None
    except ValueError as exc:
        raise BootstrapReceiveRenderError("consumer config endpoint is invalid") from exc
    if (
        parsed.scheme != "https" or parsed.hostname != f"s3.{region}.arvanstorage.ir" or parsed.path not in ("", "/")
        or parsed.query or parsed.fragment or parsed.username or parsed.password or has_port
    ):
        raise BootstrapReceiveRenderError("consumer config endpoint must be its HTTPS Arvan endpoint")
    bucket = _require_text(value.get("bucket"), field="consumer config bucket", maximum=63)
    if not BUCKET_RE.fullmatch(bucket):
        raise BootstrapReceiveRenderError("consumer config bucket is invalid")
    prefix = _require_text(value.get("prefix"), field="consumer config prefix", maximum=512).strip("/")
    if not prefix or any(not PREFIX_COMPONENT_RE.fullmatch(part) for part in prefix.split("/")):
        raise BootstrapReceiveRenderError("consumer config prefix is invalid")
    if value.get("age_binary") != "/usr/bin/age":
        raise BootstrapReceiveRenderError("consumer config must pin /usr/bin/age")
    if value.get("age_identity_file") != WA_IR_BOOTSTRAP_IDENTITY_FILE:
        raise BootstrapReceiveRenderError("consumer config must pin the WA-IR bootstrap identity path")
    _require_absolute_path(value.get("workspace"), field="consumer config workspace")
    if value.get("source_site") != "webapp_fi":
        raise BootstrapReceiveRenderError("consumer config must pin source_site to webapp_fi")
    encoded_key = _require_text(value.get("source_signing_public_key_base64"), field="consumer config public key", maximum=128)
    try:
        if len(base64.b64decode(encoded_key, validate=True)) != 32:
            raise BootstrapReceiveRenderError("consumer config public key has an unsafe length")
    except (binascii.Error, ValueError) as exc:
        raise BootstrapReceiveRenderError("consumer config public key is invalid") from exc
    fi_encoded_key = _require_text(
        value.get("webapp_fi_source_attestation_public_key_base64"),
        field="consumer config FI source attestation public key",
        maximum=128,
    )
    try:
        if len(base64.b64decode(fi_encoded_key, validate=True)) != 32:
            raise BootstrapReceiveRenderError("consumer config FI source attestation public key has an unsafe length")
    except (binascii.Error, ValueError) as exc:
        raise BootstrapReceiveRenderError("consumer config FI source attestation public key is invalid") from exc
    controller_encoded_key = _require_text(
        value.get("webapp_fi_controller_authorization_public_key_base64"),
        field="consumer config WebApp-FI controller authorization public key",
        maximum=128,
    )
    try:
        if len(base64.b64decode(controller_encoded_key, validate=True)) != 32:
            raise BootstrapReceiveRenderError(
                "consumer config WebApp-FI controller authorization public key has an unsafe length"
            )
    except (binascii.Error, ValueError) as exc:
        raise BootstrapReceiveRenderError(
            "consumer config WebApp-FI controller authorization public key is invalid"
        ) from exc
    maximum = value.get("maximum_artifact_bytes")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 100 * 1024 * 1024 * 1024:
        raise BootstrapReceiveRenderError("consumer config maximum_artifact_bytes is invalid")
    return {"endpoint": endpoint, "region": region, "bucket": bucket, "prefix": prefix}


def _read_bootstrap_archive_members(payload: bytes) -> dict[str, bytes]:
    """Read the local prepared archive without extracting it anywhere."""

    if not 1 <= len(payload) <= MAX_ARCHIVE_BYTES:
        raise BootstrapReceiveRenderError("bootstrap archive has an unsafe size")
    expected_names = set(PAYLOAD_MEMBERS) | {PACKAGE_MANIFEST_MEMBER}
    result: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            members = archive.getmembers()
            if len(members) != len(expected_names):
                raise BootstrapReceiveRenderError("bootstrap archive member count is unsupported")
            for entry in members:
                name = entry.name
                pure = PurePosixPath(name)
                if (
                    not name
                    or "\\" in name
                    or pure.as_posix() != name
                    or pure.is_absolute()
                    or any(part in ("", ".", "..") for part in pure.parts)
                    or not entry.isreg()
                    or entry.linkname
                    or entry.pax_headers
                    or getattr(entry, "sparse", None)
                    or entry.mode != 0o600
                    or entry.uid != 0
                    or entry.gid != 0
                    or entry.mtime != 0
                    or not 1 <= entry.size <= MAX_CONTROL_FILE_BYTES
                    or name in result
                ):
                    raise BootstrapReceiveRenderError("bootstrap archive has an unsafe member")
                source = archive.extractfile(entry)
                if source is None:
                    raise BootstrapReceiveRenderError("bootstrap archive member cannot be read")
                member = source.read(entry.size + 1)
                if len(member) != entry.size:
                    raise BootstrapReceiveRenderError("bootstrap archive member has an unsafe size")
                result[name] = member
    except (OSError, tarfile.TarError) as exc:
        raise BootstrapReceiveRenderError("bootstrap archive cannot be safely verified") from exc
    if set(result) != expected_names:
        raise BootstrapReceiveRenderError("bootstrap archive member schema is unsupported")
    return result


def _verify_local_bootstrap_package(*, package_directory: Path, preparation_receipt: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """Verify the exact local package that the publisher already encrypted.

    The package manifest is intentionally only an archive member.  The
    preparation receipt root binds the archive and manifest byte hashes, so no
    second manifest file is needed in the control-plane interface.
    """

    package = _require_root_private_directory(package_directory, field="bootstrap package directory")
    expected_receipt = package / PREPARATION_RECEIPT_NAME
    if preparation_receipt != expected_receipt:
        raise BootstrapReceiveRenderError("bootstrap preparation receipt must be the canonical package receipt")
    preparation_raw = _read_root_only_file(preparation_receipt, field="bootstrap preparation receipt")
    preparation = _validate_preparation_receipt(
        _parse_json(preparation_raw, field="bootstrap preparation receipt", canonical=True)
    )
    if preparation.get("package_directory") != str(package):
        raise BootstrapReceiveRenderError("bootstrap preparation receipt package_directory does not match")
    archive_raw = _read_root_only_file(
        package / PACKAGE_ARCHIVE_NAME,
        field="bootstrap archive",
        maximum_bytes=MAX_ARCHIVE_BYTES,
    )
    if (
        _sha256_bytes(archive_raw) != preparation["archive"]["sha256"]
        or len(archive_raw) != preparation["archive"]["bytes"]
    ):
        raise BootstrapReceiveRenderError("bootstrap archive does not match the preparation receipt")
    members = _read_bootstrap_archive_members(archive_raw)
    manifest_raw = members[PACKAGE_MANIFEST_MEMBER]
    package = _validate_package_manifest(
        _parse_json(manifest_raw, field="embedded bootstrap package manifest", canonical=True)
    )
    manifest_sha = _sha256_bytes(manifest_raw)
    if manifest_sha != preparation["manifest_sha256"]:
        raise BootstrapReceiveRenderError("embedded bootstrap package manifest does not match the preparation receipt")
    observed_payload_hashes = {name: _sha256_bytes(members[name]) for name in PAYLOAD_MEMBERS}
    if observed_payload_hashes != package["files"]:
        raise BootstrapReceiveRenderError("bootstrap archive payload hashes do not match the embedded manifest")
    if package["consumer_config_sha256"] != preparation["consumer_config_sha256"]:
        raise BootstrapReceiveRenderError("bootstrap preparation receipt consumer config binding is inconsistent")
    config_raw = members["config/consumer.json"]
    if _sha256_bytes(config_raw) != package["consumer_config_sha256"]:
        raise BootstrapReceiveRenderError("bootstrap consumer config does not match the embedded manifest")
    consumer = _validate_consumer_config(config_raw)
    preparation["raw_sha256"] = _sha256_bytes(preparation_raw)
    return package, preparation, consumer


def _expected_bootstrap_object_key(*, prefix: str, control_commit: str, bootstrap_id: str) -> str:
    return "/".join(
        (
            prefix,
            "bootstrap-artifacts",
            "v1",
            "webapp_fi",
            "webapp_ir",
            control_commit,
            bootstrap_id,
            "stage-consumer-bootstrap.tar.age",
        )
    )


def _validate_presigned_url(value: object, *, endpoint: str, bucket: str, object_key: str, version_id: str) -> str:
    url = _require_text(value, field="presigned URL", maximum=MAX_URL_BYTES)
    if any(character.isspace() for character in url):
        raise BootstrapReceiveRenderError("presigned URL contains whitespace")
    parsed = urlparse(url)
    endpoint_parsed = urlparse(endpoint)
    try:
        has_port = parsed.port is not None
    except ValueError as exc:
        raise BootstrapReceiveRenderError("presigned URL is invalid") from exc
    expected_path = "/" + quote(bucket, safe="") + "/" + quote(object_key, safe="/")
    if (
        parsed.scheme != "https" or parsed.hostname != endpoint_parsed.hostname or has_port or parsed.username or parsed.password
        or parsed.fragment or parsed.path != expected_path
    ):
        raise BootstrapReceiveRenderError("presigned URL is not bound to the configured Object Storage endpoint")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise BootstrapReceiveRenderError("presigned URL query is invalid") from exc
    if query.get("versionId") != [version_id]:
        raise BootstrapReceiveRenderError("presigned URL must bind exactly one matching VersionId")
    sigv4_fields = ("X-Amz-Algorithm", "X-Amz-Credential", "X-Amz-Signature")
    sigv2_fields = ("AWSAccessKeyId", "Signature", "Expires")
    sigv4 = all(query.get(name) is not None and len(query[name]) == 1 and bool(query[name][0]) for name in sigv4_fields)
    sigv2 = all(query.get(name) is not None and len(query[name]) == 1 and bool(query[name][0]) for name in sigv2_fields)
    if sigv4 == sigv2:
        raise BootstrapReceiveRenderError("presigned URL must contain exactly one supported signed-request envelope")
    return url


def _parse_published_at(value: object) -> str:
    text = _require_text(value, field="bootstrap publish receipt published_at", maximum=64)
    if not text.endswith("Z"):
        raise BootstrapReceiveRenderError("bootstrap publish receipt published_at must be UTC")
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BootstrapReceiveRenderError("bootstrap publish receipt published_at is invalid") from exc
    if parsed.tzinfo is None:
        raise BootstrapReceiveRenderError("bootstrap publish receipt published_at is invalid")
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_publish_receipt(value: object, *, preparation_raw_sha256: str, manifest_sha256: str, consumer: Mapping[str, str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BootstrapReceiveRenderError("bootstrap publish receipt must be an object")
    receipt = dict(value)
    expected = {
        "schema", "status", "source_site", "destination_site", "control_commit", "control_tree", "bootstrap_id",
        "published_at", "bootstrap",
    }
    if set(receipt) != expected or receipt.get("schema") != BOOTSTRAP_PUBLISH_RECEIPT_SCHEMA or receipt.get("status") != "published":
        raise BootstrapReceiveRenderError("bootstrap publish receipt is unsupported")
    if receipt.get("source_site") != "webapp_fi" or receipt.get("destination_site") != "webapp_ir":
        raise BootstrapReceiveRenderError("bootstrap publish receipt site binding is invalid")
    control_commit = receipt.get("control_commit")
    control_tree = receipt.get("control_tree")
    if not isinstance(control_commit, str) or not COMMIT_RE.fullmatch(control_commit):
        raise BootstrapReceiveRenderError("bootstrap publish receipt control_commit is invalid")
    if not isinstance(control_tree, str) or not COMMIT_RE.fullmatch(control_tree):
        raise BootstrapReceiveRenderError("bootstrap publish receipt control_tree is invalid")
    bootstrap_id = receipt.get("bootstrap_id")
    if not isinstance(bootstrap_id, str) or not BUNDLE_ID_RE.fullmatch(bootstrap_id):
        raise BootstrapReceiveRenderError("bootstrap publish receipt bootstrap_id is invalid")
    _parse_published_at(receipt.get("published_at"))
    bootstrap = receipt.get("bootstrap")
    expected_bootstrap = {
        "object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes",
        "manifest_sha256", "preparation_receipt_sha256", "presigned_url",
    }
    if not isinstance(bootstrap, Mapping) or set(bootstrap) != expected_bootstrap:
        raise BootstrapReceiveRenderError("bootstrap publish receipt bootstrap descriptor is unsupported")
    expected_key = _expected_bootstrap_object_key(
        prefix=consumer["prefix"], control_commit=control_commit, bootstrap_id=bootstrap_id
    )
    if bootstrap.get("object_key") != expected_key:
        raise BootstrapReceiveRenderError("bootstrap publish receipt object_key is not in the immutable bootstrap namespace")
    version_id = _require_text(bootstrap.get("version_id"), field="bootstrap publish receipt version_id", maximum=1024)
    cipher_sha = _require_sha256(bootstrap.get("ciphertext_sha256"), field="bootstrap publish receipt ciphertext_sha256")
    cipher_bytes = _require_positive_size(bootstrap.get("ciphertext_bytes"), field="bootstrap publish receipt ciphertext_bytes", maximum=MAX_CIPHERTEXT_BYTES)
    plain_sha = _require_sha256(bootstrap.get("plaintext_sha256"), field="bootstrap publish receipt plaintext_sha256")
    plain_bytes = _require_positive_size(bootstrap.get("plaintext_bytes"), field="bootstrap publish receipt plaintext_bytes", maximum=MAX_ARCHIVE_BYTES)
    if bootstrap.get("manifest_sha256") != manifest_sha256:
        raise BootstrapReceiveRenderError("bootstrap publish receipt manifest hash does not bind the trusted package manifest")
    if bootstrap.get("preparation_receipt_sha256") != preparation_raw_sha256:
        raise BootstrapReceiveRenderError("bootstrap publish receipt preparation receipt hash does not bind the trusted receipt")
    url = _validate_presigned_url(
        bootstrap.get("presigned_url"), endpoint=consumer["endpoint"], bucket=consumer["bucket"], object_key=expected_key,
        version_id=version_id,
    )
    return {
        "control_commit": control_commit,
        "control_tree": control_tree,
        "bootstrap_id": bootstrap_id,
        "object_key": expected_key,
        "version_id": version_id,
        "ciphertext_sha256": cipher_sha,
        "ciphertext_bytes": cipher_bytes,
        "plaintext_sha256": plain_sha,
        "plaintext_bytes": plain_bytes,
        "manifest_sha256": manifest_sha256,
        "preparation_receipt_sha256": preparation_raw_sha256,
        "presigned_url": url,
    }


def _build_remote_config(*, bootstrap_root: str, package: Mapping[str, Any], preparation: Mapping[str, Any], published: Mapping[str, Any], consumer: Mapping[str, str]) -> dict[str, Any]:
    if package["control"]["commit"] != preparation["control_commit"] or package["control"]["commit"] != published["control_commit"]:
        raise BootstrapReceiveRenderError("bootstrap control commit bindings disagree")
    if package["control"]["tree"] != preparation["control_tree"] or package["control"]["tree"] != published["control_tree"]:
        raise BootstrapReceiveRenderError("bootstrap control tree bindings disagree")
    if package["consumer_config_sha256"] != preparation["consumer_config_sha256"]:
        raise BootstrapReceiveRenderError("bootstrap consumer config bindings disagree")
    if preparation["archive"]["sha256"] != published["plaintext_sha256"] or preparation["archive"]["bytes"] != published["plaintext_bytes"]:
        raise BootstrapReceiveRenderError("bootstrap archive plaintext bindings disagree")
    return {
        "schema": "gold-trade-wa-ir-stage-bootstrap-receive-config-v1",
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "endpoint": consumer["endpoint"],
        "region": consumer["region"],
        "bucket": consumer["bucket"],
        "prefix": consumer["prefix"],
        "bootstrap_root": bootstrap_root,
        "age_identity_file": WA_IR_BOOTSTRAP_IDENTITY_FILE,
        "control_commit": published["control_commit"],
        "control_tree": published["control_tree"],
        "bootstrap_id": published["bootstrap_id"],
        "object_key": published["object_key"],
        "version_id": published["version_id"],
        "ciphertext_sha256": published["ciphertext_sha256"],
        "ciphertext_bytes": published["ciphertext_bytes"],
        "plaintext_sha256": published["plaintext_sha256"],
        "plaintext_bytes": published["plaintext_bytes"],
        "manifest_sha256": published["manifest_sha256"],
        "preparation_receipt_sha256": published["preparation_receipt_sha256"],
        "consumer_config_sha256": package["consumer_config_sha256"],
        "files": package["files"],
    }


# The URL is deliberately not part of this source or its JSON configuration.
# It is the final argv item after "--" in the rendered remote command.
REMOTE_RECEIVER_SOURCE = r'''
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from urllib.parse import parse_qs, quote, urlparse

SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")
PREFIX_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,127}$")
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_CIPHERTEXT_BYTES = MAX_ARCHIVE_BYTES + 2 * 1024 * 1024
MAX_MEMBER_BYTES = 2 * 1024 * 1024
MAX_URL_BYTES = 8192
PACKAGE_MANIFEST_MEMBER = "bootstrap-package.json"
RECEIPT_NAME = "bootstrap-receipt.json"
TRANSPORT_SCHEMA = "gold-trade-wa-ir-artifact-stage-v1"
OBJECT_ENCRYPTION = "age-v1"

class ReceiveError(RuntimeError):
    pass

def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReceiveError("duplicate JSON key")
        result[key] = value
    return result

def canonical_json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()

def sha256_file(path, maximum):
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise ReceiveError("size limit")
            digest.update(chunk)
    return digest.hexdigest(), total

def require_text(value, maximum=4096):
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise ReceiveError("invalid text")
    if any(ord(character) < 0x20 or ord(character) == 0x7f for character in value):
        raise ReceiveError("control character")
    return value

def require_sha256(value):
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ReceiveError("invalid sha256")
    return value

def require_size(value, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ReceiveError("invalid size")
    return value

def require_absolute_path(value):
    text = require_text(value, 1024)
    pure = PurePosixPath(text)
    if (
        not pure.is_absolute() or len(pure.parts) < 2 or pure.as_posix() != text
        or any(part in ("", ".", "..") for part in pure.parts)
    ):
        raise ReceiveError("invalid path")
    return text

def require_safe_member(name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ReceiveError("unsafe archive member")
    pure = PurePosixPath(name)
    if pure.as_posix() != name or pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise ReceiveError("unsafe archive member")
    return name

def require_trusted_executable(value):
    path = Path(value)
    try:
        original = path.lstat()
        resolved = path.resolve(strict=True)
        final = resolved.lstat()
    except OSError as exc:
        raise ReceiveError("required executable unavailable") from exc
    if original.st_uid != 0 or final.st_uid != 0 or not stat.S_ISREG(final.st_mode):
        raise ReceiveError("required executable is untrusted")
    if final.st_mode & 0o022 or not final.st_mode & 0o111:
        raise ReceiveError("required executable is untrusted")
    return str(path)

def require_root_private_file(value):
    path = Path(require_absolute_path(value))
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        after = resolved.lstat()
    except OSError as exc:
        raise ReceiveError("required identity unavailable") from exc
    if resolved != path or stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(after.st_mode):
        raise ReceiveError("required identity is unsafe")
    if after.st_uid != 0 or after.st_nlink != 1 or after.st_mode & 0o077 or after.st_size < 1:
        raise ReceiveError("required identity is unsafe")
    return path

def require_root_private_directory(value):
    path = Path(require_absolute_path(value))
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        after = resolved.lstat()
    except OSError as exc:
        raise ReceiveError("bootstrap root unavailable") from exc
    if resolved != path or stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(after.st_mode):
        raise ReceiveError("bootstrap root is unsafe")
    if after.st_uid != 0 or after.st_mode & 0o077:
        raise ReceiveError("bootstrap root is unsafe")
    return path

def validate_url(value, config):
    url = require_text(value, MAX_URL_BYTES)
    if any(character.isspace() for character in url):
        raise ReceiveError("invalid URL")
    parsed = urlparse(url)
    endpoint = urlparse(config["endpoint"])
    try:
        has_port = parsed.port is not None
    except ValueError as exc:
        raise ReceiveError("invalid URL") from exc
    expected_path = "/" + quote(config["bucket"], safe="") + "/" + quote(config["object_key"], safe="/")
    if (parsed.scheme != "https" or parsed.hostname != endpoint.hostname or has_port or parsed.username or parsed.password
            or parsed.fragment or parsed.path != expected_path):
        raise ReceiveError("URL endpoint binding")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ReceiveError("invalid URL query") from exc
    if query.get("versionId") != [config["version_id"]]:
        raise ReceiveError("URL version binding")
    sigv4_names = ("X-Amz-Algorithm", "X-Amz-Credential", "X-Amz-Signature")
    sigv2_names = ("AWSAccessKeyId", "Signature", "Expires")
    sigv4 = all(len(query.get(name, [])) == 1 and bool(query[name][0]) for name in sigv4_names)
    sigv2 = all(len(query.get(name, [])) == 1 and bool(query[name][0]) for name in sigv2_names)
    if sigv4 == sigv2:
        raise ReceiveError("URL signature binding")
    return url

def parse_header_blocks(raw):
    try:
        text = raw.decode("iso-8859-1")
    except UnicodeDecodeError as exc:
        raise ReceiveError("invalid response headers") from exc
    blocks = []
    for raw_block in re.split(r"\r?\n\r?\n", text):
        if not raw_block:
            continue
        lines = raw_block.splitlines()
        if not lines or not re.fullmatch(r"HTTP/\d(?:\.\d)?\s+\d{3}(?:\s+.*)?", lines[0]):
            raise ReceiveError("invalid response headers")
        status = int(lines[0].split()[1])
        headers = {}
        for line in lines[1:]:
            if not line or line[0] in " \t" or ":" not in line:
                raise ReceiveError("invalid response headers")
            name, header_value = line.split(":", 1)
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
                raise ReceiveError("invalid response headers")
            headers.setdefault(name.lower(), []).append(header_value.strip())
        if 300 <= status < 400 or "location" in headers:
            raise ReceiveError("redirect response")
        blocks.append((status, headers))
    if not blocks or blocks[-1][0] != 200:
        raise ReceiveError("download response is not HTTP 200")
    return blocks[-1][1]

def validate_headers(raw, config):
    headers = parse_header_blocks(raw)
    expected = {
        "x-amz-version-id": config["version_id"],
        "x-amz-meta-transport-schema": TRANSPORT_SCHEMA,
        "x-amz-meta-encryption": OBJECT_ENCRYPTION,
        "x-amz-meta-ciphertext-sha256": config["ciphertext_sha256"],
    }
    if "x-amz-server-side-encryption" in headers:
        raise ReceiveError("provider-side encryption is disallowed")
    for name, value in expected.items():
        if headers.get(name) != [value]:
            raise ReceiveError("response metadata mismatch")
    content_length = headers.get("content-length")
    if content_length is not None:
        if len(content_length) != 1 or not re.fullmatch(r"[0-9]+", content_length[0]) or int(content_length[0]) != config["ciphertext_bytes"]:
            raise ReceiveError("response length mismatch")

def parse_manifest(payload, config):
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiveError("invalid embedded manifest") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise ReceiveError("invalid embedded manifest")
    expected = {"schema", "status", "control", "files", "consumer_config_sha256"}
    if set(value) != expected or value.get("schema") != "gold-trade-wa-ir-stage-bootstrap-package-v1" or value.get("status") != "prepared":
        raise ReceiveError("embedded manifest schema")
    control = value.get("control")
    if not isinstance(control, dict) or set(control) != {"commit", "tree"}:
        raise ReceiveError("embedded manifest control")
    if control.get("commit") != config["control_commit"] or control.get("tree") != config["control_tree"]:
        raise ReceiveError("embedded manifest control binding")
    files = value.get("files")
    expected_files = config["files"]
    if not isinstance(files, dict) or files != expected_files:
        raise ReceiveError("embedded manifest files binding")
    if value.get("consumer_config_sha256") != config["consumer_config_sha256"]:
        raise ReceiveError("embedded manifest config binding")
    return value

def verify_archive(archive_path, config):
    expected_files = config["files"]
    expected_names = set(expected_files) | {PACKAGE_MANIFEST_MEMBER}
    observed = {}
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members = archive.getmembers()
            if len(members) != len(expected_names):
                raise ReceiveError("archive member count")
            for member in members:
                name = require_safe_member(member.name)
                if (not member.isreg() or member.linkname or member.pax_headers or getattr(member, "sparse", None)
                        or member.mode != 0o600 or member.uid != 0 or member.gid != 0 or member.mtime != 0
                        or member.size < 1 or member.size > MAX_MEMBER_BYTES or name in observed):
                    raise ReceiveError("unsafe archive member")
                handle = archive.extractfile(member)
                if handle is None:
                    raise ReceiveError("archive member unreadable")
                payload = handle.read(member.size + 1)
                if len(payload) != member.size:
                    raise ReceiveError("archive member size")
                observed[name] = payload
    except (OSError, tarfile.TarError) as exc:
        raise ReceiveError("archive cannot be verified") from exc
    if set(observed) != expected_names:
        raise ReceiveError("archive member set")
    manifest = observed[PACKAGE_MANIFEST_MEMBER]
    if sha256_bytes(manifest) != config["manifest_sha256"]:
        raise ReceiveError("archive manifest hash")
    parse_manifest(manifest, config)
    for name, expected_sha in expected_files.items():
        if sha256_bytes(observed[name]) != expected_sha:
            raise ReceiveError("archive member hash")
    return observed

def verify_extracted(candidate, config):
    expected_files = dict(config["files"])
    expected_names = set(expected_files) | {PACKAGE_MANIFEST_MEMBER, RECEIPT_NAME}
    actual = set()
    for root, directories, filenames in os.walk(candidate, topdown=True, followlinks=False):
        root_path = Path(root)
        for directory in list(directories):
            item = root_path / directory
            metadata = item.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0 or metadata.st_gid != 0
            ):
                raise ReceiveError("unsafe extracted directory")
            os.chmod(item, 0o700)
        for filename in filenames:
            item = root_path / filename
            relative = item.relative_to(candidate).as_posix()
            metadata = item.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != 0 or metadata.st_gid != 0
            ):
                raise ReceiveError("unsafe extracted file")
            actual.add(relative)
    # Receipt does not exist yet while this is called after tar extraction.
    if actual != set(expected_files) | {PACKAGE_MANIFEST_MEMBER}:
        raise ReceiveError("unexpected extracted member")
    for name, expected_sha in expected_files.items():
        item = candidate / name
        if sha256_file(item, MAX_MEMBER_BYTES)[0] != expected_sha:
            raise ReceiveError("extracted member hash")
        os.chmod(item, 0o600)
    manifest = candidate / PACKAGE_MANIFEST_MEMBER
    if sha256_file(manifest, MAX_MEMBER_BYTES)[0] != config["manifest_sha256"]:
        raise ReceiveError("extracted manifest hash")
    os.chmod(manifest, 0o600)
    if expected_names - (set(expected_files) | {PACKAGE_MANIFEST_MEMBER, RECEIPT_NAME}):
        raise ReceiveError("unexpected extracted state")

def write_new_private_json(path, value):
    payload = canonical_json_bytes(value) + b"\n"
    if b"https://" in payload or b"presigned" in payload.lower():
        raise ReceiveError("receipt URL persistence")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        raise
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ReceiveError("receipt mode")

def build_receipt(config, candidate, received_at=None):
    receipt = {
        "schema": "gold-trade-wa-ir-stage-bootstrap-receipt-v1",
        "status": "received",
        "received_at": received_at or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_site": config["source_site"],
        "destination_site": config["destination_site"],
        "control_commit": config["control_commit"],
        "control_tree": config["control_tree"],
        "bootstrap_id": config["bootstrap_id"],
        "candidate_directory": str(candidate),
        "files": config["files"],
        "bootstrap": {
            "object_key": config["object_key"],
            "version_id": config["version_id"],
            "ciphertext_sha256": config["ciphertext_sha256"],
            "ciphertext_bytes": config["ciphertext_bytes"],
            "plaintext_sha256": config["plaintext_sha256"],
            "plaintext_bytes": config["plaintext_bytes"],
            "package_manifest_sha256": config["manifest_sha256"],
            "preparation_receipt_sha256": config["preparation_receipt_sha256"],
            "consumer_config_sha256": config["consumer_config_sha256"],
        },
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    return receipt

def receive(config, url):
    validate_url(url, config)
    for executable in ("/usr/bin/curl", "/usr/bin/age", "/usr/bin/python3", "/usr/bin/tar"):
        require_trusted_executable(executable)
    identity = require_root_private_file(config["age_identity_file"])
    root = require_root_private_directory(config["bootstrap_root"])
    candidate = root / ("received-" + config["control_commit"] + "-" + config["bootstrap_id"])
    if candidate.parent != root or candidate.exists() or candidate.is_symlink():
        raise ReceiveError("candidate already exists")
    completed = False
    os.umask(0o077)
    try:
        os.mkdir(candidate, 0o700)
        metadata = candidate.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise ReceiveError("candidate creation")
        ciphertext = candidate / ".ciphertext"
        plaintext = candidate / ".plaintext"
        result = subprocess.run(
            ["/usr/bin/curl", "--disable", "--silent", "--show-error", "--fail", "--globoff", "--noproxy", "*",
             "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "0", "--connect-timeout", "20",
             "--max-time", "120", "--dump-header", "-", "--output", str(ciphertext), "--", url],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode != 0:
            raise ReceiveError("download failed")
        validate_headers(result.stdout, config)
        cipher_sha, cipher_bytes = sha256_file(ciphertext, MAX_CIPHERTEXT_BYTES)
        if cipher_sha != config["ciphertext_sha256"] or cipher_bytes != config["ciphertext_bytes"]:
            raise ReceiveError("ciphertext binding")
        result = subprocess.run(
            ["/usr/bin/age", "--decrypt", "--identity", str(identity), "--output", str(plaintext), str(ciphertext)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode != 0:
            raise ReceiveError("decryption failed")
        plain_sha, plain_bytes = sha256_file(plaintext, MAX_ARCHIVE_BYTES)
        if plain_sha != config["plaintext_sha256"] or plain_bytes != config["plaintext_bytes"]:
            raise ReceiveError("plaintext binding")
        verify_archive(plaintext, config)
        result = subprocess.run(
            ["/usr/bin/tar", "--extract", "--file", str(plaintext), "--directory", str(candidate), "--no-same-owner",
             "--no-same-permissions", "--numeric-owner"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode != 0:
            raise ReceiveError("archive extraction failed")
        ciphertext.unlink()
        plaintext.unlink()
        verify_extracted(candidate, config)
        receipt = build_receipt(config, candidate)
        write_new_private_json(candidate / RECEIPT_NAME, receipt)
        completed = True
        print(json.dumps({"status": "received", "candidate_directory": str(candidate), "receipt": str(candidate / RECEIPT_NAME)}, sort_keys=True))
    finally:
        if not completed and candidate.exists():
            shutil.rmtree(candidate, ignore_errors=True)

def load_config(value):
    try:
        raw = base64.b64decode(value, validate=True)
        config = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object)
    except Exception as exc:
        raise ReceiveError("receiver configuration invalid") from exc
    expected = {
        "schema", "source_site", "destination_site", "endpoint", "region", "bucket", "prefix", "bootstrap_root", "age_identity_file",
        "control_commit", "control_tree", "bootstrap_id", "object_key", "version_id", "ciphertext_sha256",
        "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes", "manifest_sha256", "preparation_receipt_sha256",
        "consumer_config_sha256", "files",
    }
    if not isinstance(config, dict) or set(config) != expected or config.get("schema") != "gold-trade-wa-ir-stage-bootstrap-receive-config-v1":
        raise ReceiveError("receiver configuration invalid")
    if config.get("source_site") != "webapp_fi" or config.get("destination_site") != "webapp_ir":
        raise ReceiveError("receiver configuration invalid")
    for key in ("endpoint", "region", "bucket", "prefix", "bootstrap_root", "age_identity_file", "control_commit", "control_tree", "bootstrap_id", "object_key", "version_id"):
        require_text(config.get(key))
    if config["age_identity_file"] != "/etc/trading-bot-three-site/wa-ir/artifact-stage-2c08.agekey":
        raise ReceiveError("receiver identity binding")
    require_absolute_path(config["bootstrap_root"])
    parsed_endpoint = urlparse(config["endpoint"])
    try:
        endpoint_has_port = parsed_endpoint.port is not None
    except ValueError as exc:
        raise ReceiveError("receiver endpoint binding") from exc
    if (parsed_endpoint.scheme != "https" or parsed_endpoint.hostname != "s3." + config["region"] + ".arvanstorage.ir"
            or parsed_endpoint.path not in ("", "/") or parsed_endpoint.query or parsed_endpoint.fragment
            or parsed_endpoint.username or parsed_endpoint.password or endpoint_has_port):
        raise ReceiveError("receiver endpoint binding")
    if not BUCKET_RE.fullmatch(config["bucket"]):
        raise ReceiveError("receiver bucket binding")
    normalized_prefix = config["prefix"].strip("/")
    if not normalized_prefix or normalized_prefix != config["prefix"] or any(not PREFIX_COMPONENT_RE.fullmatch(part) for part in normalized_prefix.split("/")):
        raise ReceiveError("receiver prefix binding")
    if not COMMIT_RE.fullmatch(config["control_commit"]) or not COMMIT_RE.fullmatch(config["control_tree"]) or not BUNDLE_ID_RE.fullmatch(config["bootstrap_id"]):
        raise ReceiveError("receiver configuration invalid")
    expected_key = "/".join((
        config["prefix"], "bootstrap-artifacts", "v1", "webapp_fi", "webapp_ir", config["control_commit"],
        config["bootstrap_id"], "stage-consumer-bootstrap.tar.age",
    ))
    if config["object_key"] != expected_key:
        raise ReceiveError("receiver object key binding")
    for key in ("ciphertext_sha256", "plaintext_sha256", "manifest_sha256", "preparation_receipt_sha256", "consumer_config_sha256"):
        require_sha256(config[key])
    config["ciphertext_bytes"] = require_size(config["ciphertext_bytes"], MAX_CIPHERTEXT_BYTES)
    config["plaintext_bytes"] = require_size(config["plaintext_bytes"], MAX_ARCHIVE_BYTES)
    files = config.get("files")
    required_names = {
        "scripts/manage_webapp_ir_artifact_stage.py", "scripts/manage_webapp_ir_snapshot.py",
        "scripts/manage_webapp_ir_release_provenance.py", "core/standby_snapshot_capacity.py",
        "scripts/webapp_ir_image_archive_contract.py",
        "config/consumer.json",
    }
    if not isinstance(files, dict) or set(files) != required_names:
        raise ReceiveError("receiver file binding")
    for name, digest in files.items():
        require_safe_member(name)
        require_sha256(digest)
    if files["config/consumer.json"] != config["consumer_config_sha256"]:
        raise ReceiveError("receiver config hash binding")
    return config

def main():
    try:
        if len(sys.argv) != 5 or sys.argv[3] != "--":
            raise ReceiveError("invalid receive arguments")
        config = load_config(sys.argv[2])
        receive(config, sys.argv[4])
    except Exception:
        # Never emit the presigned URL, curl diagnostics, or response headers.
        print(json.dumps({"status": "blocked", "error": "bootstrap receive verification failed"}, sort_keys=True))
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''


REMOTE_LAUNCHER = (
    "import base64,sys;exec(compile(base64.b64decode(sys.argv[1]),'<wa-ir-bootstrap-receive>','exec'))"
)


def render_receive_command(
    *,
    publish_receipt: Path | None = None,
    publish_receipt_bytes: bytes | None = None,
    bootstrap_package_directory: Path,
    preparation_receipt: Path,
    bootstrap_root: str,
) -> str:
    """Validate local inputs and return one SSH control command without executing it."""

    bootstrap_root = _require_installer_compatible_path(bootstrap_root, field="bootstrap root")
    if (publish_receipt is None) == (publish_receipt_bytes is None):
        raise BootstrapReceiveRenderError("provide exactly one bootstrap publish receipt source")
    if publish_receipt is not None:
        publish_raw = _read_root_only_file(publish_receipt, field="bootstrap publish receipt")
    else:
        if not isinstance(publish_receipt_bytes, bytes):
            raise BootstrapReceiveRenderError("bootstrap publish receipt bytes are invalid")
        if not publish_receipt_bytes or len(publish_receipt_bytes) > MAX_CONTROL_FILE_BYTES:
            raise BootstrapReceiveRenderError("bootstrap publish receipt bytes exceed the fixed size bound")
        publish_raw = publish_receipt_bytes
    package, preparation, consumer = _verify_local_bootstrap_package(
        package_directory=bootstrap_package_directory,
        preparation_receipt=preparation_receipt,
    )
    published = _validate_publish_receipt(
        _parse_json(publish_raw, field="bootstrap publish receipt", canonical=False),
        preparation_raw_sha256=preparation["raw_sha256"],
        manifest_sha256=preparation["manifest_sha256"],
        consumer=consumer,
    )
    remote_config = _build_remote_config(
        bootstrap_root=bootstrap_root,
        package=package,
        preparation=preparation,
        published=published,
        consumer=consumer,
    )
    program_b64 = base64.b64encode(REMOTE_RECEIVER_SOURCE.encode("utf-8")).decode("ascii")
    config_b64 = base64.b64encode(_canonical_json_bytes(remote_config)).decode("ascii")
    # `remote` is one SSH argument.  Its independently quoted argv keeps a
    # signed URL (including hostile shell metacharacters) only as final argv.
    remote = shlex.join([
        "/usr/bin/python3", "-I", "-B", "-c", REMOTE_LAUNCHER, program_b64, config_b64, "--",
        published["presigned_url"],
    ])
    return shlex.join(["ssh", *SSH_OPTIONS, REMOTE_HOST, remote])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    publish_receipt = parser.add_mutually_exclusive_group(required=True)
    publish_receipt.add_argument("--publish-receipt", type=Path)
    publish_receipt.add_argument(
        "--publish-receipt-stdin",
        action="store_true",
        help="read one just-published receipt from stdin without writing its presigned URL to disk",
    )
    parser.add_argument("--bootstrap-package-directory", required=True, type=Path)
    parser.add_argument("--preparation-receipt", required=True, type=Path)
    parser.add_argument("--bootstrap-root", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        publish_receipt_bytes = _read_publish_receipt_stdin() if arguments.publish_receipt_stdin else None
        print(
            render_receive_command(
                publish_receipt=arguments.publish_receipt,
                publish_receipt_bytes=publish_receipt_bytes,
                bootstrap_package_directory=arguments.bootstrap_package_directory,
                preparation_receipt=arguments.preparation_receipt,
                bootstrap_root=arguments.bootstrap_root,
            )
        )
    except BootstrapReceiveRenderError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
