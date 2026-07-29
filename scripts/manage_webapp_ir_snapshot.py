#!/usr/bin/env python3
"""Publish and consume immutable encrypted WebApp standby snapshots.

This is deliberately a transport primitive, not a deployment script.  A
publisher receives a PostgreSQL custom-format dump and an ``uploads/`` tar.gz
created by a local host wrapper, encrypts each artifact for the destination
age recipient, and writes immutable objects to a private, versioned S3 bucket.
The manifest is the commit marker and is uploaded last.  A consumer downloads
only the exact object versions recorded by a verified manifest, decrypts them
into a new candidate directory, and writes a root-only readiness receipt.

The source wrapper must report the database consistent-snapshot start and the
completion time for all source artifacts.  Freshness is always measured from
that database snapshot start, never from object upload time.

The script never starts containers, restores a database, changes ``current``,
or deletes/overwrites an S3 object.  The host-specific deployment wrapper owns
the explicitly named standby volumes and turns a ready receipt into a restore
receipt after its own restore verification.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import hashlib
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
from urllib.parse import urlparse

import fcntl

try:  # Imported lazily enough for focused unit tests to replace the client.
    import boto3
except ImportError:  # pragma: no cover - production images already ship boto3.
    boto3 = None  # type: ignore[assignment]


TRANSPORT_SCHEMA = "gold-trade-snapshot-transport-v1"
MANIFEST_SCHEMA = "gold-trade-snapshot-manifest-v1"
READY_RECEIPT_SCHEMA = "gold-trade-snapshot-ready-v1"
OBJECT_ENCRYPTION = "age-v1"
OBJECT_LAYOUT_VERSION = "v1"
DEFAULT_WORKSPACE = "/srv/trading-bot/production-data/snapshot-transport"
DEFAULT_MAXIMUM_SNAPSHOT_AGE_SECONDS = 30
DEFAULT_MAXIMUM_DATABASE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAXIMUM_UPLOADS_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_MAXIMUM_AUDIT_BYTES = 2 * 1024 * 1024 * 1024
MAXIMUM_SOURCE_DB_CLIENT_LIFETIME_SECONDS = 300

SITE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SNAPSHOT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{16,64}$")
RELEASE_SHA_RE = re.compile(r"^[a-f0-9]{40,64}$")
ALEMBIC_REVISION_RE = re.compile(r"^[A-Za-z0-9_]{4,128}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{20,128}$")
SAFE_PREFIX_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,127}$")


class SnapshotTransportError(RuntimeError):
    """Raised when the fail-closed snapshot transport contract is violated."""


@dataclasses.dataclass(frozen=True)
class TransportConfig:
    endpoint: str
    region: str
    bucket: str
    prefix: str
    credentials_file: Path
    age_binary: str
    age_recipient: str | None
    age_identity_file: Path | None
    workspace: Path
    maximum_database_bytes: int
    maximum_uploads_bytes: int
    maximum_audit_bytes: int
    maximum_snapshot_age_seconds: int


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
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
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def utc_iso(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise SnapshotTransportError("timestamp must be timezone-aware")
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc_iso(value: object, *, field: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SnapshotTransportError(f"{field} must be an RFC3339 UTC timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotTransportError(f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise SnapshotTransportError(f"{field} must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SnapshotTransportError(f"{field} must be a non-empty string")
    return value


def require_id(value: object, field: str, pattern: re.Pattern[str]) -> str:
    text = require_string(value, field)
    if not pattern.fullmatch(text):
        raise SnapshotTransportError(f"{field} has an unsafe format")
    return text


def require_nonnegative_int(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SnapshotTransportError(f"{field} must be an integer >= {minimum}")
    return value


def _lstat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except FileNotFoundError as exc:
        raise SnapshotTransportError(f"required path does not exist: {path}") from exc


def require_root_only_file(path: Path, *, field: str, require_executable: bool = False) -> Path:
    if not path.is_absolute():
        raise SnapshotTransportError(f"{field} must be an absolute path")
    state = _lstat(path)
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode):
        raise SnapshotTransportError(f"{field} must be a regular non-symlink file")
    if state.st_uid != 0:
        raise SnapshotTransportError(f"{field} must be owned by root")
    if stat.S_IMODE(state.st_mode) & 0o077:
        raise SnapshotTransportError(f"{field} must not be readable or writable by group/other")
    if require_executable and not (stat.S_IMODE(state.st_mode) & 0o100):
        raise SnapshotTransportError(f"{field} must be executable by root")
    return path


def require_secure_input_file(path: Path, *, field: str, maximum_bytes: int) -> Path:
    require_root_only_file(path, field=field)
    state = _lstat(path)
    if state.st_size <= 0:
        raise SnapshotTransportError(f"{field} must not be empty")
    if state.st_size > maximum_bytes:
        raise SnapshotTransportError(f"{field} exceeds its configured size bound")
    return path


def ensure_root_only_directory(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise SnapshotTransportError(f"{field} must be an absolute path")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        if not component:
            continue
        current = current / component
        try:
            state = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                state = current.lstat()
            else:
                state = current.lstat()
        if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
            raise SnapshotTransportError(f"{field} contains a non-directory or symlink component")
        if state.st_uid != 0:
            raise SnapshotTransportError(f"{field} must be owned by root")
        if stat.S_IMODE(state.st_mode) & 0o077:
            raise SnapshotTransportError(f"{field} must not be accessible by group/other")
    return path


def load_root_only_json(path: Path, *, field: str) -> dict[str, Any]:
    require_root_only_file(path, field=field)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotTransportError(f"{field} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise SnapshotTransportError(f"{field} must contain a JSON object")
    return payload


def validate_s3_endpoint(endpoint: object, region: object) -> tuple[str, str]:
    endpoint_text = require_string(endpoint, "endpoint")
    region_text = require_string(region, "region")
    parsed = urlparse(endpoint_text)
    expected_host = f"s3.{region_text}.arvanstorage.ir"
    if parsed.scheme != "https" or parsed.hostname != expected_host or parsed.path not in {"", "/"}:
        raise SnapshotTransportError("endpoint must be the HTTPS Arvan S3 endpoint for the configured region")
    if parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port is not None:
        raise SnapshotTransportError("endpoint must not contain credentials, a port, query, or fragment")
    return endpoint_text.rstrip("/"), region_text


def validate_prefix(value: object) -> str:
    prefix = require_string(value, "prefix").strip("/")
    components = prefix.split("/")
    if not prefix or any(not SAFE_PREFIX_COMPONENT_RE.fullmatch(component) for component in components):
        raise SnapshotTransportError("prefix must consist of safe non-empty object-key components")
    return prefix


def load_transport_config(path: Path, *, workspace_override: str | None = None) -> TransportConfig:
    raw = load_root_only_json(path, field="config")
    if raw.get("schema") != TRANSPORT_SCHEMA:
        raise SnapshotTransportError("config schema is unsupported")
    endpoint, region = validate_s3_endpoint(raw.get("endpoint"), raw.get("region"))
    bucket = require_id(raw.get("bucket"), "bucket", re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$"))
    prefix = validate_prefix(raw.get("prefix"))
    credentials_file = Path(require_string(raw.get("credentials_file"), "credentials_file"))
    age_binary = require_string(raw.get("age_binary", "/usr/bin/age"), "age_binary")
    if not os.path.isabs(age_binary):
        raise SnapshotTransportError("age_binary must be an absolute path")
    age_recipient_value = raw.get("age_recipient")
    age_recipient = None
    if age_recipient_value is not None:
        age_recipient = require_id(age_recipient_value, "age_recipient", AGE_RECIPIENT_RE)
    age_identity_value = raw.get("age_identity_file")
    age_identity_file = None
    if age_identity_value is not None:
        age_identity_file = Path(require_string(age_identity_value, "age_identity_file"))
    workspace_value = workspace_override or raw.get("workspace") or DEFAULT_WORKSPACE
    workspace = Path(require_string(workspace_value, "workspace"))
    maximum_database_bytes = require_nonnegative_int(
        raw.get("maximum_database_bytes", DEFAULT_MAXIMUM_DATABASE_BYTES),
        "maximum_database_bytes",
        minimum=1,
    )
    maximum_uploads_bytes = require_nonnegative_int(
        raw.get("maximum_uploads_bytes", DEFAULT_MAXIMUM_UPLOADS_BYTES),
        "maximum_uploads_bytes",
        minimum=1,
    )
    maximum_audit_bytes = require_nonnegative_int(
        raw.get("maximum_audit_bytes", DEFAULT_MAXIMUM_AUDIT_BYTES),
        "maximum_audit_bytes",
        minimum=1,
    )
    maximum_snapshot_age_seconds = require_nonnegative_int(
        raw.get("maximum_snapshot_age_seconds", DEFAULT_MAXIMUM_SNAPSHOT_AGE_SECONDS),
        "maximum_snapshot_age_seconds",
        minimum=1,
    )
    return TransportConfig(
        endpoint=endpoint,
        region=region,
        bucket=bucket,
        prefix=prefix,
        credentials_file=credentials_file,
        age_binary=age_binary,
        age_recipient=age_recipient,
        age_identity_file=age_identity_file,
        workspace=workspace,
        maximum_database_bytes=maximum_database_bytes,
        maximum_uploads_bytes=maximum_uploads_bytes,
        maximum_audit_bytes=maximum_audit_bytes,
        maximum_snapshot_age_seconds=maximum_snapshot_age_seconds,
    )


def load_credentials(path: Path) -> dict[str, str]:
    raw = load_root_only_json(path, field="credentials_file")
    access_key = require_string(raw.get("access_key"), "credentials_file.access_key")
    secret_key = require_string(raw.get("secret_key"), "credentials_file.secret_key")
    result = {"access_key": access_key, "secret_key": secret_key}
    session_token = raw.get("session_token")
    if session_token is not None:
        result["session_token"] = require_string(session_token, "credentials_file.session_token")
    return result


def create_s3_client(config: TransportConfig) -> Any:
    if boto3 is None:  # pragma: no cover - imports are present in deployment images.
        raise SnapshotTransportError("boto3 is unavailable")
    credentials = load_credentials(config.credentials_file)
    session = boto3.session.Session(
        aws_access_key_id=credentials["access_key"],
        aws_secret_access_key=credentials["secret_key"],
        aws_session_token=credentials.get("session_token"),
        region_name=config.region,
    )
    return session.client("s3", endpoint_url=config.endpoint)


def assert_private_versioned_bucket(client: Any, bucket: str) -> None:
    try:
        versioning = client.get_bucket_versioning(Bucket=bucket)
    except Exception as exc:
        raise SnapshotTransportError("cannot verify bucket versioning") from exc
    if not isinstance(versioning, Mapping) or versioning.get("Status") != "Enabled":
        raise SnapshotTransportError("bucket versioning must be Enabled")
    try:
        acl = client.get_bucket_acl(Bucket=bucket)
    except Exception as exc:
        raise SnapshotTransportError("cannot verify bucket privacy") from exc
    grants = acl.get("Grants") if isinstance(acl, Mapping) else None
    if not isinstance(grants, list):
        raise SnapshotTransportError("bucket ACL response is malformed")
    for grant in grants:
        if not isinstance(grant, Mapping):
            raise SnapshotTransportError("bucket ACL response is malformed")
        grantee = grant.get("Grantee")
        if not isinstance(grantee, Mapping):
            raise SnapshotTransportError("bucket ACL response is malformed")
        uri = grantee.get("URI")
        if uri in {
            "http://acs.amazonaws.com/groups/global/AllUsers",
            "http://acs.amazonaws.com/groups/global/AuthenticatedUsers",
        }:
            raise SnapshotTransportError("bucket must not grant public or authenticated-users access")


def generated_snapshot_id(now: dt.datetime | None = None) -> str:
    value = now or utc_now()
    return value.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(12)


def snapshot_base_key(config: TransportConfig, source_site: str, generation: str, snapshot_id: str) -> str:
    return "/".join(
        (
            config.prefix,
            "snapshots",
            OBJECT_LAYOUT_VERSION,
            source_site,
            generation,
            snapshot_id,
        )
    )


def validate_database_dump(path: Path, maximum_bytes: int) -> tuple[str, int]:
    require_secure_input_file(path, field="database_dump", maximum_bytes=maximum_bytes)
    with path.open("rb") as handle:
        if handle.read(5) != b"PGDMP":
            raise SnapshotTransportError("database_dump must be a pg_dump --format=custom artifact")
    return sha256_file(path)


def validate_rooted_tar_gz(path: Path, maximum_bytes: int, *, field: str, root_name: str) -> tuple[str, int]:
    require_secure_input_file(path, field=field, maximum_bytes=maximum_bytes)
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            saw_root = False
            for member in archive:
                name = member.name
                if name.rstrip("/") == root_name:
                    saw_root = True
                if name.rstrip("/") != root_name and not name.startswith(root_name + "/"):
                    raise SnapshotTransportError(f"{field} must be rooted at {root_name}/")
                path_parts = Path(name).parts
                if name.startswith("/") or ".." in path_parts or any(part == "" for part in path_parts):
                    raise SnapshotTransportError(f"{field} contains an unsafe member path")
                if not (member.isdir() or member.isreg()):
                    raise SnapshotTransportError(f"{field} may contain only regular files and directories")
            if not saw_root:
                raise SnapshotTransportError(f"{field} is missing its {root_name} root member")
    except (OSError, tarfile.TarError) as exc:
        raise SnapshotTransportError(f"{field} must be a valid gzip-compressed tar archive") from exc
    return sha256_file(path)


def validate_uploads_archive(path: Path, maximum_bytes: int) -> tuple[str, int]:
    return validate_rooted_tar_gz(path, maximum_bytes, field="uploads_archive", root_name="uploads")


def validate_audit_archive(path: Path, maximum_bytes: int) -> tuple[str, int]:
    return validate_rooted_tar_gz(path, maximum_bytes, field="audit_archive", root_name="audit_trail")


def validate_source_database_capture(mode: object, lifetime_seconds: object) -> dict[str, Any]:
    """Bind a publisher to an ephemeral read-only local DB capture contract.

    The transport intentionally does not open a database connection.  The host
    wrapper creates the ``pg_dump --format=custom`` artifact with a dedicated
    local, read-only credential and reports the actual lifetime here.  There is
    no peer host, SSH, or acknowledgement endpoint in this interface.
    """

    if mode != "short_lived_read_only":
        raise SnapshotTransportError("source_db_client_mode must be short_lived_read_only")
    lifetime = require_nonnegative_int(lifetime_seconds, "source_db_client_lifetime_seconds", minimum=1)
    if lifetime > MAXIMUM_SOURCE_DB_CLIENT_LIFETIME_SECONDS:
        raise SnapshotTransportError("source_db_client_lifetime_seconds exceeds the short-lived bound")
    return {"client_mode": "short_lived_read_only", "client_lifetime_seconds": lifetime}


def validate_source_volume_capture(mode: object) -> dict[str, str]:
    if mode != "read_only_no_mutation":
        raise SnapshotTransportError("source_volume_capture_mode must be read_only_no_mutation")
    return {"mode": "read_only_no_mutation"}


def private_child_umask() -> None:
    """Ensure age creates destination artifacts as root-only files."""

    os.umask(0o077)


def run_age_encrypt(age_binary: str, recipient: str, input_path: Path, output_path: Path) -> None:
    require_id(recipient, "age_recipient", AGE_RECIPIENT_RE)
    if output_path.exists():
        raise SnapshotTransportError("refusing to overwrite an encrypted workspace artifact")
    try:
        completed = subprocess.run(
            [age_binary, "-r", recipient, "-o", str(output_path), str(input_path)],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
            preexec_fn=private_child_umask,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SnapshotTransportError("age encryption command failed to start") from exc
    if completed.returncode != 0:
        raise SnapshotTransportError("age encryption failed")
    require_root_only_file(output_path, field="encrypted workspace artifact")


def run_age_decrypt(age_binary: str, identity_file: Path, input_path: Path, output_path: Path) -> None:
    require_root_only_file(identity_file, field="age_identity_file")
    if output_path.exists():
        raise SnapshotTransportError("refusing to overwrite a candidate artifact")
    try:
        completed = subprocess.run(
            [age_binary, "--decrypt", "-i", str(identity_file), "-o", str(output_path), str(input_path)],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
            preexec_fn=private_child_umask,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SnapshotTransportError("age decryption command failed to start") from exc
    if completed.returncode != 0:
        raise SnapshotTransportError("age decryption failed")
    require_root_only_file(output_path, field="decrypted candidate artifact")


def _not_found_error(exc: Exception) -> bool:
    if isinstance(exc, (FileNotFoundError, KeyError)):
        return True
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        error = response.get("Error")
        if isinstance(error, Mapping) and str(error.get("Code")) in {"404", "NoSuchKey", "NotFound", "NoSuchVersion"}:
            return True
    return False


def assert_object_absent(client: Any, *, bucket: str, key: str) -> None:
    try:
        client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _not_found_error(exc):
            return
        raise SnapshotTransportError("cannot confirm that an immutable object key is unused") from exc
    raise SnapshotTransportError("refusing to overwrite an existing immutable object key")


def metadata_for_ciphertext(ciphertext_sha256: str) -> dict[str, str]:
    return {
        "transport-schema": TRANSPORT_SCHEMA,
        "encryption": OBJECT_ENCRYPTION,
        "ciphertext-sha256": ciphertext_sha256,
    }


def _response_metadata(response: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = response.get("Metadata", {})
    if not isinstance(metadata, Mapping):
        raise SnapshotTransportError("object metadata is malformed")
    return metadata


def write_response_body(response: Mapping[str, Any], output_path: Path) -> tuple[str, int]:
    body = response.get("Body")
    if body is None or not hasattr(body, "read"):
        raise SnapshotTransportError("object response has no readable body")
    digest = hashlib.sha256()
    total = 0
    try:
        with output_path.open("xb") as handle:
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise SnapshotTransportError("object body returned non-bytes data")
                handle.write(chunk)
                digest.update(chunk)
                total += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise SnapshotTransportError("refusing to overwrite a local artifact") from exc
    return digest.hexdigest(), total


def get_exact_object_to_file(
    client: Any,
    *,
    bucket: str,
    key: str,
    version_id: str | None,
    output_path: Path,
    expected_sha256: str | None = None,
    expected_bytes: int | None = None,
) -> tuple[str, int, str]:
    request: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if version_id is not None:
        request["VersionId"] = version_id
    try:
        response = client.get_object(**request)
    except Exception as exc:
        raise SnapshotTransportError("cannot read the requested immutable object version") from exc
    if not isinstance(response, Mapping):
        raise SnapshotTransportError("object read returned a malformed response")
    response_version = response.get("VersionId")
    if not isinstance(response_version, str) or not response_version:
        raise SnapshotTransportError("object read did not identify its VersionId")
    if version_id is not None and response_version != version_id:
        raise SnapshotTransportError("object read returned a different VersionId")
    if response.get("ServerSideEncryption"):
        raise SnapshotTransportError("provider-side object encryption is not permitted for this transport")
    digest, total = write_response_body(response, output_path)
    metadata = _response_metadata(response)
    if metadata.get("transport-schema") != TRANSPORT_SCHEMA or metadata.get("encryption") != OBJECT_ENCRYPTION:
        raise SnapshotTransportError("object metadata does not identify the required encryption transport")
    if metadata.get("ciphertext-sha256") != digest:
        raise SnapshotTransportError("object metadata ciphertext digest does not match read-back data")
    if expected_sha256 is not None and digest != expected_sha256:
        raise SnapshotTransportError("object ciphertext digest does not match the manifest")
    if expected_bytes is not None and total != expected_bytes:
        raise SnapshotTransportError("object ciphertext byte count does not match the manifest")
    return digest, total, response_version


def verify_remote_ciphertext_in_workspace(
    client: Any,
    *,
    bucket: str,
    key: str,
    version_id: str,
    expected_sha256: str,
    expected_bytes: int,
    workspace: Path,
) -> None:
    target = workspace / ("readback-" + secrets.token_hex(12) + ".age")
    get_exact_object_to_file(
        client,
        bucket=bucket,
        key=key,
        version_id=version_id,
        output_path=target,
        expected_sha256=expected_sha256,
        expected_bytes=expected_bytes,
    )
    target.unlink()


def upload_immutable_object_in_workspace(
    client: Any,
    *,
    bucket: str,
    key: str,
    file_path: Path,
    workspace: Path,
) -> dict[str, Any]:
    assert_object_absent(client, bucket=bucket, key=key)
    ciphertext_sha256, ciphertext_bytes = sha256_file(file_path)
    with file_path.open("rb") as handle:
        try:
            response = client.put_object(
                Bucket=bucket,
                Key=key,
                Body=handle,
                ContentType="application/octet-stream",
                Metadata=metadata_for_ciphertext(ciphertext_sha256),
            )
        except Exception as exc:
            raise SnapshotTransportError("immutable object upload failed") from exc
    if not isinstance(response, Mapping):
        raise SnapshotTransportError("object upload returned a malformed response")
    version_id = response.get("VersionId")
    if not isinstance(version_id, str) or not version_id or version_id == "null":
        raise SnapshotTransportError("versioned object upload did not return a VersionId")
    verify_remote_ciphertext_in_workspace(
        client,
        bucket=bucket,
        key=key,
        version_id=version_id,
        expected_sha256=ciphertext_sha256,
        expected_bytes=ciphertext_bytes,
        workspace=workspace,
    )
    return {
        "object_key": key,
        "version_id": version_id,
        "ciphertext_sha256": ciphertext_sha256,
        "ciphertext_bytes": ciphertext_bytes,
    }


def build_manifest(
    *,
    source_site: str,
    destination_site: str,
    generation: str,
    snapshot_id: str,
    release_sha: str,
    alembic_revision: str,
    source_db_snapshot_started_at: str,
    source_capture_completed_at: str,
    published_at: str,
    source_database_capture: Mapping[str, Any],
    source_volume_capture: Mapping[str, Any],
    database: Mapping[str, Any],
    uploads: Mapping[str, Any],
    audit: Mapping[str, Any] | None,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "status": "committed",
        "source_site": source_site,
        "destination_site": destination_site,
        "source_generation": generation,
        "snapshot_id": snapshot_id,
        "release_sha": release_sha,
        "alembic_revision": alembic_revision,
        "source_db_snapshot_started_at": source_db_snapshot_started_at,
        "source_capture_completed_at": source_capture_completed_at,
        "published_at": published_at,
        "source_database_capture": dict(source_database_capture),
        "source_volume_capture": dict(source_volume_capture),
        "database": dict(database),
        "uploads": dict(uploads),
    }
    if audit is not None:
        manifest["audit"] = dict(audit)
    return manifest


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise SnapshotTransportError("refusing to overwrite a local JSON artifact")
    encoded = canonical_json_bytes(payload) + b"\n"
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(8))
    try:
        descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _artifact_descriptor(
    *,
    artifact: str,
    plaintext_sha256: str,
    plaintext_bytes: int,
    remote: Mapping[str, Any],
) -> dict[str, Any]:
    formats = {
        "database": "pg_dump_custom",
        "uploads": "tar_gz_uploads_root",
        "audit": "tar_gz_audit_trail_root",
    }
    if artifact not in formats:
        raise SnapshotTransportError("unsupported artifact kind")
    return {
        "format": formats[artifact],
        "sha256": plaintext_sha256,
        "bytes": plaintext_bytes,
        **dict(remote),
    }


def _require_artifact_descriptor(value: object, *, field: str, expected_format: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SnapshotTransportError(f"manifest {field} must be an object")
    descriptor = dict(value)
    if descriptor.get("format") != expected_format:
        raise SnapshotTransportError(f"manifest {field} has an unsupported format")
    descriptor["sha256"] = require_id(descriptor.get("sha256"), f"manifest {field}.sha256", re.compile(r"^[a-f0-9]{64}$"))
    descriptor["bytes"] = require_nonnegative_int(descriptor.get("bytes"), f"manifest {field}.bytes", minimum=1)
    descriptor["object_key"] = require_string(descriptor.get("object_key"), f"manifest {field}.object_key")
    descriptor["version_id"] = require_string(descriptor.get("version_id"), f"manifest {field}.version_id")
    descriptor["ciphertext_sha256"] = require_id(
        descriptor.get("ciphertext_sha256"), f"manifest {field}.ciphertext_sha256", re.compile(r"^[a-f0-9]{64}$")
    )
    descriptor["ciphertext_bytes"] = require_nonnegative_int(
        descriptor.get("ciphertext_bytes"), f"manifest {field}.ciphertext_bytes", minimum=1
    )
    return descriptor


def validate_manifest(
    value: object,
    *,
    config: TransportConfig,
    expected_source_site: str,
    expected_destination_site: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SnapshotTransportError("manifest must be a JSON object")
    manifest = dict(value)
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("status") != "committed":
        raise SnapshotTransportError("manifest schema or status is unsupported")
    source_site = require_id(manifest.get("source_site"), "manifest source_site", SITE_RE)
    destination_site = require_id(manifest.get("destination_site"), "manifest destination_site", SITE_RE)
    if source_site != expected_source_site or destination_site != expected_destination_site:
        raise SnapshotTransportError("manifest site binding does not match this consumer")
    generation = require_id(manifest.get("source_generation"), "manifest source_generation", GENERATION_RE)
    snapshot_id = require_id(manifest.get("snapshot_id"), "manifest snapshot_id", SNAPSHOT_ID_RE)
    release_sha = require_id(manifest.get("release_sha"), "manifest release_sha", RELEASE_SHA_RE)
    alembic_revision = require_id(manifest.get("alembic_revision"), "manifest alembic_revision", ALEMBIC_REVISION_RE)
    source_db_snapshot_started_at = require_string(
        manifest.get("source_db_snapshot_started_at"), "manifest source_db_snapshot_started_at"
    )
    source_db_snapshot_time = parse_utc_iso(
        source_db_snapshot_started_at, field="manifest source_db_snapshot_started_at"
    )
    source_capture_completed_at = require_string(
        manifest.get("source_capture_completed_at"), "manifest source_capture_completed_at"
    )
    source_capture_time = parse_utc_iso(
        source_capture_completed_at, field="manifest source_capture_completed_at"
    )
    if source_capture_time < source_db_snapshot_time:
        raise SnapshotTransportError("manifest source_capture_completed_at precedes source_db_snapshot_started_at")
    published_at = require_string(manifest.get("published_at"), "manifest published_at")
    published_time = parse_utc_iso(published_at, field="manifest published_at")
    if published_time < source_capture_time:
        raise SnapshotTransportError("manifest published_at precedes source_capture_completed_at")
    source_database_capture = validate_source_database_capture(
        (manifest.get("source_database_capture") or {}).get("client_mode")
        if isinstance(manifest.get("source_database_capture"), Mapping)
        else None,
        (manifest.get("source_database_capture") or {}).get("client_lifetime_seconds")
        if isinstance(manifest.get("source_database_capture"), Mapping)
        else None,
    )
    source_volume_capture = validate_source_volume_capture(
        (manifest.get("source_volume_capture") or {}).get("mode")
        if isinstance(manifest.get("source_volume_capture"), Mapping)
        else None
    )
    database = _require_artifact_descriptor(manifest.get("database"), field="database", expected_format="pg_dump_custom")
    uploads = _require_artifact_descriptor(manifest.get("uploads"), field="uploads", expected_format="tar_gz_uploads_root")
    audit: dict[str, Any] | None = None
    if "audit" in manifest:
        audit = _require_artifact_descriptor(
            manifest.get("audit"), field="audit", expected_format="tar_gz_audit_trail_root"
        )
    base = snapshot_base_key(config, source_site, generation, snapshot_id)
    expected_db_key = base + "/database.dump.age"
    expected_uploads_key = base + "/uploads.tar.gz.age"
    if database["object_key"] != expected_db_key or uploads["object_key"] != expected_uploads_key:
        raise SnapshotTransportError("manifest artifact keys do not match their immutable snapshot location")
    if audit is not None and audit["object_key"] != base + "/audit.tar.gz.age":
        raise SnapshotTransportError("manifest audit key does not match its immutable snapshot location")
    validated: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "status": "committed",
        "source_site": source_site,
        "destination_site": destination_site,
        "source_generation": generation,
        "snapshot_id": snapshot_id,
        "release_sha": release_sha,
        "alembic_revision": alembic_revision,
        "source_db_snapshot_started_at": source_db_snapshot_started_at,
        "source_capture_completed_at": source_capture_completed_at,
        "published_at": published_at,
        "source_database_capture": source_database_capture,
        "source_volume_capture": source_volume_capture,
        "database": database,
        "uploads": uploads,
    }
    if audit is not None:
        validated["audit"] = audit
    return validated


def _workspace_context(config: TransportConfig) -> tempfile.TemporaryDirectory[str]:
    ensure_root_only_directory(config.workspace, field="workspace")
    return tempfile.TemporaryDirectory(prefix="snapshot-", dir=str(config.workspace))


@contextlib.contextmanager
def exclusive_workspace_lock(workspace: Path, *, name: str) -> Any:
    """Prevent a delayed timer invocation from overlapping a snapshot cycle."""

    ensure_root_only_directory(workspace, field="workspace")
    if not SAFE_PREFIX_COMPONENT_RE.fullmatch(name):
        raise SnapshotTransportError("workspace lock name is unsafe")
    lock_path = workspace / ("." + name + ".lock")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(lock_path), flags, 0o600)
    except OSError as exc:
        raise SnapshotTransportError("cannot safely open the snapshot workspace lock") from exc
    try:
        require_root_only_file(lock_path, field="snapshot workspace lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SnapshotTransportError("another snapshot cycle is already running") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@contextlib.contextmanager
def locked_workspace_context(config: TransportConfig, *, lock_name: str) -> Any:
    with exclusive_workspace_lock(config.workspace, name=lock_name):
        with _workspace_context(config) as workspace:
            yield workspace


def publish_snapshot(
    client: Any,
    *,
    config: TransportConfig,
    database_dump: Path,
    uploads_archive: Path,
    audit_archive: Path | None,
    source_site: str,
    destination_site: str,
    generation: str,
    release_sha: str,
    alembic_revision: str,
    source_db_snapshot_started_at: str,
    source_capture_completed_at: str,
    source_db_client_mode: str,
    source_db_client_lifetime_seconds: int,
    source_volume_capture_mode: str,
    snapshot_id: str | None = None,
    age_recipient: str | None = None,
    now: dt.datetime | None = None,
    encryptor: Callable[[str, str, Path, Path], None] = run_age_encrypt,
) -> dict[str, Any]:
    source_site = require_id(source_site, "source_site", SITE_RE)
    destination_site = require_id(destination_site, "destination_site", SITE_RE)
    if source_site == destination_site:
        raise SnapshotTransportError("source_site and destination_site must differ")
    generation = require_id(generation, "generation", GENERATION_RE)
    release_sha = require_id(release_sha, "release_sha", RELEASE_SHA_RE)
    alembic_revision = require_id(alembic_revision, "alembic_revision", ALEMBIC_REVISION_RE)
    source_db_snapshot_started_at = require_string(
        source_db_snapshot_started_at, "source_db_snapshot_started_at"
    )
    source_db_snapshot_time = parse_utc_iso(
        source_db_snapshot_started_at, field="source_db_snapshot_started_at"
    )
    source_capture_completed_at = require_string(source_capture_completed_at, "source_capture_completed_at")
    source_capture_time = parse_utc_iso(
        source_capture_completed_at, field="source_capture_completed_at"
    )
    if source_capture_time < source_db_snapshot_time:
        raise SnapshotTransportError("source_capture_completed_at precedes source_db_snapshot_started_at")
    source_database_capture = validate_source_database_capture(
        source_db_client_mode, source_db_client_lifetime_seconds
    )
    source_volume_capture = validate_source_volume_capture(source_volume_capture_mode)
    snapshot_id = require_id(snapshot_id or generated_snapshot_id(now), "snapshot_id", SNAPSHOT_ID_RE)
    recipient = require_id(age_recipient or config.age_recipient, "age_recipient", AGE_RECIPIENT_RE)
    database_sha256, database_bytes = validate_database_dump(database_dump, config.maximum_database_bytes)
    uploads_sha256, uploads_bytes = validate_uploads_archive(uploads_archive, config.maximum_uploads_bytes)
    audit_sha256: str | None = None
    audit_bytes: int | None = None
    if audit_archive is not None:
        audit_sha256, audit_bytes = validate_audit_archive(audit_archive, config.maximum_audit_bytes)
    assert_private_versioned_bucket(client, config.bucket)
    publish_time = now or utc_now()
    published_at = utc_iso(publish_time)
    snapshot_age_seconds = int((publish_time.astimezone(dt.timezone.utc) - source_db_snapshot_time).total_seconds())
    if snapshot_age_seconds < 0 or snapshot_age_seconds > config.maximum_snapshot_age_seconds:
        raise SnapshotTransportError("source snapshot is outside the configured freshness bound before publication")
    base = snapshot_base_key(config, source_site, generation, snapshot_id)

    with locked_workspace_context(config, lock_name=f"publish-{source_site}-{destination_site}") as temporary_name:
        temporary = Path(temporary_name)
        database_ciphertext = temporary / "database.dump.age"
        uploads_ciphertext = temporary / "uploads.tar.gz.age"
        audit_ciphertext = temporary / "audit.tar.gz.age" if audit_archive is not None else None
        encryptor(config.age_binary, recipient, database_dump, database_ciphertext)
        encryptor(config.age_binary, recipient, uploads_archive, uploads_ciphertext)
        if audit_archive is not None and audit_ciphertext is not None:
            encryptor(config.age_binary, recipient, audit_archive, audit_ciphertext)
        require_root_only_file(database_ciphertext, field="database ciphertext")
        require_root_only_file(uploads_ciphertext, field="uploads ciphertext")
        if audit_ciphertext is not None:
            require_root_only_file(audit_ciphertext, field="audit ciphertext")
        database_remote = upload_immutable_object_in_workspace(
            client,
            bucket=config.bucket,
            key=base + "/database.dump.age",
            file_path=database_ciphertext,
            workspace=temporary,
        )
        uploads_remote = upload_immutable_object_in_workspace(
            client,
            bucket=config.bucket,
            key=base + "/uploads.tar.gz.age",
            file_path=uploads_ciphertext,
            workspace=temporary,
        )
        audit_remote: dict[str, Any] | None = None
        if audit_ciphertext is not None:
            audit_remote = upload_immutable_object_in_workspace(
                client,
                bucket=config.bucket,
                key=base + "/audit.tar.gz.age",
                file_path=audit_ciphertext,
                workspace=temporary,
            )
        database = _artifact_descriptor(
            artifact="database",
            plaintext_sha256=database_sha256,
            plaintext_bytes=database_bytes,
            remote=database_remote,
        )
        uploads = _artifact_descriptor(
            artifact="uploads",
            plaintext_sha256=uploads_sha256,
            plaintext_bytes=uploads_bytes,
            remote=uploads_remote,
        )
        audit = (
            _artifact_descriptor(
                artifact="audit",
                plaintext_sha256=audit_sha256,
                plaintext_bytes=audit_bytes,
                remote=audit_remote,
            )
            if audit_remote is not None and audit_sha256 is not None and audit_bytes is not None
            else None
        )
        manifest = build_manifest(
            source_site=source_site,
            destination_site=destination_site,
            generation=generation,
            snapshot_id=snapshot_id,
            release_sha=release_sha,
            alembic_revision=alembic_revision,
            source_db_snapshot_started_at=source_db_snapshot_started_at,
            source_capture_completed_at=source_capture_completed_at,
            published_at=published_at,
            source_database_capture=source_database_capture,
            source_volume_capture=source_volume_capture,
            database=database,
            uploads=uploads,
            audit=audit,
        )
        manifest_plaintext = temporary / "manifest.json"
        atomic_write_json(manifest_plaintext, manifest)
        manifest_ciphertext = temporary / "manifest.json.age"
        encryptor(config.age_binary, recipient, manifest_plaintext, manifest_ciphertext)
        require_root_only_file(manifest_ciphertext, field="manifest ciphertext")
        manifest_remote = upload_immutable_object_in_workspace(
            client,
            bucket=config.bucket,
            key=base + "/manifest.json.age",
            file_path=manifest_ciphertext,
            workspace=temporary,
        )
    result: dict[str, Any] = {
        "schema": "gold-trade-snapshot-publish-receipt-v1",
        "status": "published",
        "source_site": source_site,
        "destination_site": destination_site,
        "source_generation": generation,
        "snapshot_id": snapshot_id,
        "release_sha": release_sha,
        "alembic_revision": alembic_revision,
        "source_db_snapshot_started_at": source_db_snapshot_started_at,
        "source_capture_completed_at": source_capture_completed_at,
        "published_at": published_at,
        "source_database_capture": source_database_capture,
        "source_volume_capture": source_volume_capture,
        "database": database,
        "uploads": uploads,
        "manifest": manifest_remote,
    }
    if audit is not None:
        result["audit"] = audit
    return result


def list_manifest_objects(client: Any, *, bucket: str, prefix: str) -> list[tuple[str, dt.datetime]]:
    objects: list[tuple[str, dt.datetime]] = []
    continuation: str | None = None
    while True:
        request: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if continuation is not None:
            request["ContinuationToken"] = continuation
        try:
            response = client.list_objects_v2(**request)
        except Exception as exc:
            raise SnapshotTransportError("cannot list immutable snapshot manifests") from exc
        if not isinstance(response, Mapping):
            raise SnapshotTransportError("object listing response is malformed")
        contents = response.get("Contents", [])
        if not isinstance(contents, list):
            raise SnapshotTransportError("object listing response is malformed")
        for entry in contents:
            if not isinstance(entry, Mapping):
                raise SnapshotTransportError("object listing entry is malformed")
            key = entry.get("Key")
            modified = entry.get("LastModified")
            if isinstance(key, str) and key.endswith("/manifest.json.age"):
                if not isinstance(modified, dt.datetime) or modified.tzinfo is None:
                    raise SnapshotTransportError("manifest listing entry is missing a timezone-aware LastModified")
                objects.append((key, modified.astimezone(dt.timezone.utc)))
        if not response.get("IsTruncated"):
            break
        continuation = response.get("NextContinuationToken")
        if not isinstance(continuation, str) or not continuation:
            raise SnapshotTransportError("object listing pagination token is missing")
    return sorted(objects, key=lambda entry: (entry[1], entry[0]), reverse=True)


def decrypt_manifest_to_value(
    *,
    config: TransportConfig,
    encrypted_manifest: Path,
    output_path: Path,
    identity_file: Path,
    decryptor: Callable[[str, Path, Path, Path], None],
) -> dict[str, Any]:
    decryptor(config.age_binary, identity_file, encrypted_manifest, output_path)
    try:
        value = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotTransportError("decrypted manifest is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SnapshotTransportError("decrypted manifest must be a JSON object")
    return value


def discover_latest_manifest(
    client: Any,
    *,
    config: TransportConfig,
    source_site: str,
    destination_site: str,
    identity_file: Path,
    workspace: Path,
    decryptor: Callable[[str, Path, Path, Path], None] = run_age_decrypt,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_site = require_id(source_site, "source_site", SITE_RE)
    destination_site = require_id(destination_site, "destination_site", SITE_RE)
    source_prefix = "/".join((config.prefix, "snapshots", OBJECT_LAYOUT_VERSION, source_site)) + "/"
    objects = list_manifest_objects(client, bucket=config.bucket, prefix=source_prefix)
    if not objects:
        raise SnapshotTransportError("no committed snapshot manifest exists for the requested source")
    for index, (key, _modified) in enumerate(objects):
        encrypted_manifest = workspace / f"manifest-{index}.json.age"
        plaintext_manifest = workspace / f"manifest-{index}.json"
        try:
            ciphertext_sha256, ciphertext_bytes, version_id = get_exact_object_to_file(
                client,
                bucket=config.bucket,
                key=key,
                version_id=None,
                output_path=encrypted_manifest,
            )
            manifest = decrypt_manifest_to_value(
                config=config,
                encrypted_manifest=encrypted_manifest,
                output_path=plaintext_manifest,
                identity_file=identity_file,
                decryptor=decryptor,
            )
            validated = validate_manifest(
                manifest,
                config=config,
                expected_source_site=source_site,
                expected_destination_site=destination_site,
            )
            expected_key = snapshot_base_key(
                config,
                validated["source_site"],
                validated["source_generation"],
                validated["snapshot_id"],
            ) + "/manifest.json.age"
            if key != expected_key:
                raise SnapshotTransportError("manifest object key does not match its encrypted manifest binding")
            return validated, {
                "object_key": key,
                "version_id": version_id,
                "ciphertext_sha256": ciphertext_sha256,
                "ciphertext_bytes": ciphertext_bytes,
            }
        finally:
            encrypted_manifest.unlink(missing_ok=True)
            plaintext_manifest.unlink(missing_ok=True)
    raise SnapshotTransportError("no usable committed snapshot manifest exists for the requested source")


def _safe_candidate_directory(candidate_root: Path, manifest: Mapping[str, Any]) -> Path:
    ensure_root_only_directory(candidate_root, field="candidate_root")
    source_site = require_id(manifest.get("source_site"), "manifest source_site", SITE_RE)
    generation = require_id(manifest.get("source_generation"), "manifest source_generation", GENERATION_RE)
    snapshot_id = require_id(manifest.get("snapshot_id"), "manifest snapshot_id", SNAPSHOT_ID_RE)
    return candidate_root / source_site / generation / snapshot_id


def _prepare_candidate_parent(candidate: Path) -> None:
    parent = candidate.parent
    ensure_root_only_directory(parent, field="candidate_root")
    if candidate.exists() or candidate.is_symlink():
        raise SnapshotTransportError("refusing to overwrite an existing candidate snapshot")


def _consume_artifact(
    client: Any,
    *,
    config: TransportConfig,
    descriptor: Mapping[str, Any],
    encrypted_output: Path,
    plaintext_output: Path,
    identity_file: Path,
    decryptor: Callable[[str, Path, Path, Path], None],
    validator: Callable[[Path, int], tuple[str, int]],
) -> None:
    get_exact_object_to_file(
        client,
        bucket=config.bucket,
        key=require_string(descriptor.get("object_key"), "artifact object_key"),
        version_id=require_string(descriptor.get("version_id"), "artifact version_id"),
        output_path=encrypted_output,
        expected_sha256=require_string(descriptor.get("ciphertext_sha256"), "artifact ciphertext_sha256"),
        expected_bytes=require_nonnegative_int(descriptor.get("ciphertext_bytes"), "artifact ciphertext_bytes", minimum=1),
    )
    decryptor(config.age_binary, identity_file, encrypted_output, plaintext_output)
    encrypted_output.unlink(missing_ok=True)
    plaintext_sha256, plaintext_bytes = validator(plaintext_output, require_nonnegative_int(descriptor.get("bytes"), "artifact bytes", minimum=1))
    if plaintext_sha256 != descriptor.get("sha256") or plaintext_bytes != descriptor.get("bytes"):
        raise SnapshotTransportError("decrypted artifact does not match its committed manifest digest")


def build_ready_receipt(
    *,
    manifest: Mapping[str, Any],
    manifest_remote: Mapping[str, Any],
    candidate: Path,
    ready_at: str,
    source_db_snapshot_age_seconds: int,
    source_capture_age_seconds: int,
    source_capture_duration_seconds: int,
    publish_lag_seconds: int,
) -> dict[str, Any]:
    database = dict(manifest["database"])
    uploads = dict(manifest["uploads"])
    receipt: dict[str, Any] = {
        "schema": READY_RECEIPT_SCHEMA,
        "status": "ready",
        "source_site": manifest["source_site"],
        "destination_site": manifest["destination_site"],
        "source_generation": manifest["source_generation"],
        "snapshot_id": manifest["snapshot_id"],
        "release_sha": manifest["release_sha"],
        "alembic_revision": manifest["alembic_revision"],
        "source_db_snapshot_started_at": manifest["source_db_snapshot_started_at"],
        "source_capture_completed_at": manifest["source_capture_completed_at"],
        "published_at": manifest["published_at"],
        "source_database_capture": dict(manifest["source_database_capture"]),
        "source_volume_capture": dict(manifest["source_volume_capture"]),
        "ready_at": ready_at,
        "snapshot_age_seconds": source_db_snapshot_age_seconds,
        "source_db_snapshot_age_seconds": source_db_snapshot_age_seconds,
        "source_capture_age_seconds": source_capture_age_seconds,
        "source_capture_duration_seconds": source_capture_duration_seconds,
        "publish_lag_seconds": publish_lag_seconds,
        "database_dump_path": str(candidate / "database.dump"),
        "uploads_archive_path": str(candidate / "uploads.tar.gz"),
        "candidate_directory": str(candidate),
        "database": database,
        "uploads": uploads,
        "manifest": dict(manifest_remote),
    }
    if "audit" in manifest:
        receipt["audit_archive_path"] = str(candidate / "audit.tar.gz")
        receipt["audit"] = dict(manifest["audit"])
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    return receipt


def consume_snapshot(
    client: Any,
    *,
    config: TransportConfig,
    source_site: str,
    destination_site: str,
    candidate_root: Path,
    identity_file: Path | None = None,
    now: dt.datetime | None = None,
    decryptor: Callable[[str, Path, Path, Path], None] = run_age_decrypt,
) -> dict[str, Any]:
    selected_identity = identity_file or config.age_identity_file
    if selected_identity is None:
        raise SnapshotTransportError("age_identity_file is required for a snapshot consumer")
    selected_identity = require_root_only_file(selected_identity, field="age_identity_file")
    assert_private_versioned_bucket(client, config.bucket)
    ensure_root_only_directory(config.workspace, field="workspace")
    now_value = now or utc_now()
    with locked_workspace_context(config, lock_name=f"consume-{source_site}-{destination_site}") as temporary_name:
        temporary = Path(temporary_name)
        manifest, manifest_remote = discover_latest_manifest(
            client,
            config=config,
            source_site=source_site,
            destination_site=destination_site,
            identity_file=selected_identity,
            workspace=temporary,
            decryptor=decryptor,
        )
        source_db_snapshot_time = parse_utc_iso(
            manifest["source_db_snapshot_started_at"], field="manifest source_db_snapshot_started_at"
        )
        source_capture_time = parse_utc_iso(
            manifest["source_capture_completed_at"], field="manifest source_capture_completed_at"
        )
        published_at = parse_utc_iso(manifest["published_at"], field="manifest published_at")
        snapshot_age_seconds = int((now_value.astimezone(dt.timezone.utc) - source_db_snapshot_time).total_seconds())
        if snapshot_age_seconds < 0 or snapshot_age_seconds > config.maximum_snapshot_age_seconds:
            raise SnapshotTransportError("newest committed snapshot is outside the configured freshness bound")
        source_capture_age_seconds = int((now_value.astimezone(dt.timezone.utc) - source_capture_time).total_seconds())
        source_capture_duration_seconds = int((source_capture_time - source_db_snapshot_time).total_seconds())
        if source_capture_age_seconds < 0 or source_capture_duration_seconds < 0:
            raise SnapshotTransportError("manifest source capture timestamps are inconsistent")
        publish_lag_seconds = int((published_at - source_capture_time).total_seconds())
        if publish_lag_seconds < 0:
            raise SnapshotTransportError("manifest publication precedes source capture completion")
        candidate = _safe_candidate_directory(candidate_root, manifest)
        _prepare_candidate_parent(candidate)
        candidate_parent = candidate.parent
        incoming = candidate_parent / (".incoming-" + manifest["snapshot_id"] + "-" + secrets.token_hex(8))
        try:
            incoming.mkdir(mode=0o700)
            _consume_artifact(
                client,
                config=config,
                descriptor=manifest["database"],
                encrypted_output=incoming / "database.dump.age",
                plaintext_output=incoming / "database.dump",
                identity_file=selected_identity,
                decryptor=decryptor,
                validator=validate_database_dump,
            )
            _consume_artifact(
                client,
                config=config,
                descriptor=manifest["uploads"],
                encrypted_output=incoming / "uploads.tar.gz.age",
                plaintext_output=incoming / "uploads.tar.gz",
                identity_file=selected_identity,
                decryptor=decryptor,
                validator=validate_uploads_archive,
            )
            if "audit" in manifest:
                _consume_artifact(
                    client,
                    config=config,
                    descriptor=manifest["audit"],
                    encrypted_output=incoming / "audit.tar.gz.age",
                    plaintext_output=incoming / "audit.tar.gz",
                    identity_file=selected_identity,
                    decryptor=decryptor,
                    validator=validate_audit_archive,
                )
            ready_at = utc_iso(now_value)
            receipt = build_ready_receipt(
                manifest=manifest,
                manifest_remote=manifest_remote,
                candidate=candidate,
                ready_at=ready_at,
                source_db_snapshot_age_seconds=snapshot_age_seconds,
                source_capture_age_seconds=source_capture_age_seconds,
                source_capture_duration_seconds=source_capture_duration_seconds,
                publish_lag_seconds=publish_lag_seconds,
            )
            atomic_write_json(incoming / "snapshot-ready.json", receipt)
            if candidate.exists() or candidate.is_symlink():
                raise SnapshotTransportError("refusing to overwrite an existing candidate snapshot")
            os.replace(incoming, candidate)
            return receipt
        except Exception:
            shutil.rmtree(incoming, ignore_errors=True)
            raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish = subparsers.add_parser("publish", help="publish a new immutable encrypted snapshot")
    publish.add_argument("--config", required=True, type=Path)
    publish.add_argument("--workspace", default=None)
    publish.add_argument("--database-dump", required=True, type=Path)
    publish.add_argument("--uploads-archive", required=True, type=Path)
    publish.add_argument("--audit-archive", default=None, type=Path)
    publish.add_argument("--source-site", required=True)
    publish.add_argument("--destination-site", required=True)
    publish.add_argument("--generation", required=True)
    publish.add_argument("--release-sha", required=True)
    publish.add_argument("--alembic-revision", required=True)
    publish.add_argument("--source-db-snapshot-started-at", required=True)
    publish.add_argument("--source-capture-completed-at", required=True)
    publish.add_argument("--source-db-client-mode", required=True, choices=("short_lived_read_only",))
    publish.add_argument("--source-db-client-lifetime-seconds", required=True, type=int)
    publish.add_argument("--source-volume-capture-mode", required=True, choices=("read_only_no_mutation",))
    publish.add_argument("--snapshot-id", default=None)
    publish.add_argument("--age-recipient", default=None)

    consume = subparsers.add_parser("consume", help="stage the newest verified snapshot into a new candidate directory")
    consume.add_argument("--config", required=True, type=Path)
    consume.add_argument("--workspace", default=None)
    consume.add_argument("--source-site", required=True)
    consume.add_argument("--destination-site", required=True)
    consume.add_argument("--candidate-root", required=True, type=Path)
    consume.add_argument("--age-identity-file", default=None, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_transport_config(args.config, workspace_override=args.workspace)
        client = create_s3_client(config)
        if args.command == "publish":
            result = publish_snapshot(
                client,
                config=config,
                database_dump=args.database_dump,
                uploads_archive=args.uploads_archive,
                audit_archive=args.audit_archive,
                source_site=args.source_site,
                destination_site=args.destination_site,
                generation=args.generation,
                release_sha=args.release_sha,
                alembic_revision=args.alembic_revision,
                source_db_snapshot_started_at=args.source_db_snapshot_started_at,
                source_capture_completed_at=args.source_capture_completed_at,
                source_db_client_mode=args.source_db_client_mode,
                source_db_client_lifetime_seconds=args.source_db_client_lifetime_seconds,
                source_volume_capture_mode=args.source_volume_capture_mode,
                snapshot_id=args.snapshot_id,
                age_recipient=args.age_recipient,
            )
        elif args.command == "consume":
            result = consume_snapshot(
                client,
                config=config,
                source_site=args.source_site,
                destination_site=args.destination_site,
                candidate_root=args.candidate_root,
                identity_file=args.age_identity_file,
            )
        else:  # argparse makes this unreachable, but keep the command fail-closed.
            raise SnapshotTransportError("unsupported command")
    except SnapshotTransportError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
