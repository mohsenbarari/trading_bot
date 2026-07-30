#!/usr/bin/env python3
"""Publish and stage immutable, age-encrypted WA-IR release artifacts.

This is an Object Storage transport primitive only.  It is intentionally
limited to moving root-only, opaque artifacts such as a Git release bundle or
a Docker image tar through a private, versioned Arvan S3 bucket.  It never
loads an image, starts a container, changes ``current``, restores data, or
changes public routing.

The publisher conditionally creates one unique object per encrypted artifact
and one encrypted, source-signed manifest.  Every upload is immediately read
back by its exact VersionId.  The manifest contains only version-bound,
short-lived download URLs for the encrypted artifacts and is itself encrypted
for the destination age identity.

The consumer accepts a version-bound presigned manifest URL as an argument.
It rejects redirects, arbitrary hosts, mutable object versions, provider-side
encryption, malformed metadata, bad signatures, and content hash mismatches.
It decrypts only into a fresh, detached candidate directory and records a
root-only receipt without retaining the presigned URLs.  A later, explicitly
authorised host operation may inspect that detached candidate and perform a
Docker load or release stage; this tool does neither.

Before that consumer exists on WA-IR, ``publish-bootstrap`` can create one
separate encrypted, create-only consumer-package object.  Its returned URL is
transient control-plane output for a later authorised Object-Storage-only
bootstrap downloader; it is not a release manifest and does not install or
activate anything by itself.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import dataclasses
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence


def _load_snapshot_primitives() -> Any:
    """Load the existing hardened snapshot primitives in both script/test modes."""

    try:
        import manage_webapp_ir_snapshot as snapshot  # type: ignore[import-not-found]

        return snapshot
    except ModuleNotFoundError:
        module_path = Path(__file__).with_name("manage_webapp_ir_snapshot.py")
        spec = importlib.util.spec_from_file_location("_webapp_ir_snapshot_primitives", module_path)
        if spec is None or spec.loader is None:  # pragma: no cover - local repository invariant.
            raise RuntimeError("cannot load snapshot transport primitives")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module


snapshot = _load_snapshot_primitives()


def _load_bootstrap_preparation_primitives() -> Any:
    """Load local-only bootstrap verification only for publisher-side use.

    The extracted WA-IR consumer intentionally does not contain the preparation
    helper, so this remains lazy and is never needed by ``consume``.
    """

    module_name = "prepare_webapp_ir_stage_bootstrap"
    try:
        return __import__(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
    module_path = Path(__file__).with_name(module_name + ".py")
    spec = importlib.util.spec_from_file_location("_webapp_ir_bootstrap_preparation", module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - local repository invariant.
        raise RuntimeError("cannot load bootstrap preparation primitives")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
except ImportError:  # pragma: no cover - deployment requirements already include cryptography.
    InvalidSignature = None  # type: ignore[assignment,misc]
    serialization = None  # type: ignore[assignment]
    Ed25519PrivateKey = None  # type: ignore[assignment,misc]
    Ed25519PublicKey = None  # type: ignore[assignment,misc]


CONFIG_SCHEMA = "gold-trade-wa-ir-artifact-stage-config-v1"
MANIFEST_SCHEMA = "gold-trade-wa-ir-artifact-stage-manifest-v1"
PUBLISH_RECEIPT_SCHEMA = "gold-trade-wa-ir-artifact-stage-publish-receipt-v1"
STAGE_RECEIPT_SCHEMA = "gold-trade-wa-ir-artifact-stage-receipt-v1"
BOOTSTRAP_PUBLISH_RECEIPT_SCHEMA = "gold-trade-wa-ir-artifact-stage-bootstrap-publish-receipt-v1"
TRANSPORT_SCHEMA = "gold-trade-wa-ir-artifact-stage-v1"
MANIFEST_SIGNATURE_DOMAIN = b"gold-trade-wa-ir-artifact-stage-manifest-v1\x00"
MANIFEST_SIGNATURE_ALGORITHM = "ed25519"
OBJECT_ENCRYPTION = "age-v1"
OBJECT_LAYOUT_VERSION = "v1"
DEFAULT_MAXIMUM_ARTIFACT_BYTES = 20 * 1024 * 1024 * 1024
DEFAULT_PRESIGN_EXPIRES_SECONDS = 300
MAXIMUM_MANIFEST_CIPHERTEXT_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 60

SITE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
RELEASE_SHA_RE = re.compile(r"^[a-f0-9]{40,64}$")
BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ARTIFACT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
BINDING_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")
PREFIX_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,127}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class ArtifactStageError(RuntimeError):
    """Raised when the immutable artifact staging contract is violated."""


@dataclasses.dataclass(frozen=True)
class PublisherConfig:
    endpoint: str
    region: str
    bucket: str
    prefix: str
    credentials_file: Path
    age_binary: str
    age_recipient: str
    workspace: Path
    source_site: str
    source_signing_private_key_file: Path
    maximum_artifact_bytes: int
    presign_expires_seconds: int


@dataclasses.dataclass(frozen=True)
class ConsumerConfig:
    endpoint: str
    region: str
    bucket: str
    prefix: str
    age_binary: str
    age_identity_file: Path
    workspace: Path
    source_site: str
    source_signing_public_key: bytes
    maximum_artifact_bytes: int


@dataclasses.dataclass(frozen=True)
class ArtifactInput:
    name: str
    path: Path
    bindings: Mapping[str, str] = dataclasses.field(default_factory=dict)


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> tuple[str, int]:
    return snapshot.sha256_file(path)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def utc_iso(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise ArtifactStageError("timestamp must be timezone-aware")
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ArtifactStageError(f"{field} must be a non-empty string")
    return value


def require_id(value: object, field: str, pattern: re.Pattern[str]) -> str:
    text = require_string(value, field)
    if not pattern.fullmatch(text):
        raise ArtifactStageError(f"{field} has an unsafe format")
    return text


def require_positive_int(value: object, field: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ArtifactStageError(f"{field} must be a positive integer")
    if maximum is not None and value > maximum:
        raise ArtifactStageError(f"{field} exceeds its configured maximum")
    return value


def require_version_id(value: object, field: str) -> str:
    version_id = require_string(value, field)
    if len(version_id) > 1024 or any(ord(character) < 0x20 or ord(character) == 0x7F for character in version_id):
        raise ArtifactStageError(f"{field} has an unsafe format")
    return version_id


def require_absolute_path(value: object, field: str) -> Path:
    path = Path(require_string(value, field))
    if not path.is_absolute():
        raise ArtifactStageError(f"{field} must be an absolute path")
    return path


def _snapshot_error(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except snapshot.SnapshotTransportError as exc:
        raise ArtifactStageError(str(exc)) from exc


def validate_prefix(value: object) -> str:
    prefix = require_string(value, "prefix").strip("/")
    components = prefix.split("/")
    if not prefix or any(not PREFIX_COMPONENT_RE.fullmatch(component) for component in components):
        raise ArtifactStageError("prefix must consist of safe non-empty object-key components")
    return prefix


def decode_exact_base64(value: object, *, field: str, expected_bytes: int) -> bytes:
    encoded = require_string(value, field)
    try:
        decoded = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ArtifactStageError(f"{field} must be strict base64") from exc
    if len(decoded) != expected_bytes:
        raise ArtifactStageError(f"{field} must decode to exactly {expected_bytes} bytes")
    return decoded


def load_root_only_json(path: Path, *, field: str) -> dict[str, Any]:
    return _snapshot_error(lambda: snapshot.load_root_only_json(path, field=field))


def validate_endpoint(endpoint: object, region: object) -> tuple[str, str]:
    return _snapshot_error(lambda: snapshot.validate_s3_endpoint(endpoint, region))


def _require_known_fields(raw: Mapping[str, Any], *, allowed: set[str], role: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise ArtifactStageError(f"{role} config has unsupported fields")


def _load_common_config(raw: Mapping[str, Any], *, role: str) -> tuple[str, str, str, str, str, Path, int]:
    if raw.get("schema") != CONFIG_SCHEMA:
        raise ArtifactStageError("config schema is unsupported")
    endpoint, region = validate_endpoint(raw.get("endpoint"), raw.get("region"))
    bucket = require_id(raw.get("bucket"), "bucket", BUCKET_RE)
    prefix = validate_prefix(raw.get("prefix"))
    age_binary = require_string(raw.get("age_binary", "/usr/bin/age"), "age_binary")
    if not os.path.isabs(age_binary):
        raise ArtifactStageError("age_binary must be an absolute path")
    workspace = require_absolute_path(raw.get("workspace"), "workspace")
    maximum_artifact_bytes = require_positive_int(
        raw.get("maximum_artifact_bytes", DEFAULT_MAXIMUM_ARTIFACT_BYTES),
        "maximum_artifact_bytes",
        maximum=100 * 1024 * 1024 * 1024,
    )
    return endpoint, region, bucket, prefix, age_binary, workspace, maximum_artifact_bytes


def load_publisher_config(path: Path) -> PublisherConfig:
    raw = load_root_only_json(path, field="publisher config")
    _require_known_fields(
        raw,
        allowed={
            "schema",
            "endpoint",
            "region",
            "bucket",
            "prefix",
            "credentials_file",
            "age_binary",
            "age_recipient",
            "workspace",
            "source_site",
            "source_signing_private_key_file",
            "maximum_artifact_bytes",
            "presign_expires_seconds",
        },
        role="publisher",
    )
    endpoint, region, bucket, prefix, age_binary, workspace, maximum_artifact_bytes = _load_common_config(raw, role="publisher")
    credentials_file = require_absolute_path(raw.get("credentials_file"), "credentials_file")
    age_recipient = require_id(raw.get("age_recipient"), "age_recipient", snapshot.AGE_RECIPIENT_RE)
    source_site = require_id(raw.get("source_site"), "source_site", SITE_RE)
    signing_key = require_absolute_path(raw.get("source_signing_private_key_file"), "source_signing_private_key_file")
    expires = require_positive_int(
        raw.get("presign_expires_seconds", DEFAULT_PRESIGN_EXPIRES_SECONDS),
        "presign_expires_seconds",
        maximum=900,
    )
    if expires < 60:
        raise ArtifactStageError("presign_expires_seconds must be at least 60")
    return PublisherConfig(
        endpoint=endpoint,
        region=region,
        bucket=bucket,
        prefix=prefix,
        credentials_file=credentials_file,
        age_binary=age_binary,
        age_recipient=age_recipient,
        workspace=workspace,
        source_site=source_site,
        source_signing_private_key_file=signing_key,
        maximum_artifact_bytes=maximum_artifact_bytes,
        presign_expires_seconds=expires,
    )


def load_consumer_config(path: Path) -> ConsumerConfig:
    raw = load_root_only_json(path, field="consumer config")
    _require_known_fields(
        raw,
        allowed={
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
            "maximum_artifact_bytes",
        },
        role="consumer",
    )
    endpoint, region, bucket, prefix, age_binary, workspace, maximum_artifact_bytes = _load_common_config(raw, role="consumer")
    identity = require_absolute_path(raw.get("age_identity_file"), "age_identity_file")
    source_site = require_id(raw.get("source_site"), "source_site", SITE_RE)
    source_public_key = decode_exact_base64(
        raw.get("source_signing_public_key_base64"),
        field="source_signing_public_key_base64",
        expected_bytes=32,
    )
    return ConsumerConfig(
        endpoint=endpoint,
        region=region,
        bucket=bucket,
        prefix=prefix,
        age_binary=age_binary,
        age_identity_file=identity,
        workspace=workspace,
        source_site=source_site,
        source_signing_public_key=source_public_key,
        maximum_artifact_bytes=maximum_artifact_bytes,
    )


def create_s3_client(config: PublisherConfig) -> Any:
    if snapshot.boto3 is None:  # pragma: no cover - deployment image invariant.
        raise ArtifactStageError("boto3 is unavailable")
    credentials = _snapshot_error(lambda: snapshot.load_credentials(config.credentials_file))
    try:
        from botocore.config import Config as BotocoreConfig

        session = snapshot.boto3.session.Session(
            aws_access_key_id=credentials["access_key"],
            aws_secret_access_key=credentials["secret_key"],
            aws_session_token=credentials.get("session_token"),
            region_name=config.region,
        )
        # Path-style URLs make the consumer's host and exact object path binding deterministic.
        return session.client(
            "s3",
            endpoint_url=config.endpoint,
            config=BotocoreConfig(s3={"addressing_style": "path"}),
        )
    except Exception as exc:  # pragma: no cover - exercised only on real hosts.
        raise ArtifactStageError("cannot create the Object Storage client") from exc


def require_root_only_input(path: Path, *, field: str, maximum_bytes: int) -> Path:
    result = _snapshot_error(
        lambda: snapshot.require_secure_input_file(path, field=field, maximum_bytes=maximum_bytes)
    )
    return result


def require_private_workspace(path: Path, *, field: str) -> Path:
    return _snapshot_error(lambda: snapshot.ensure_root_only_directory(path, field=field))


def require_private_file(path: Path, *, field: str) -> Path:
    return _snapshot_error(lambda: snapshot.require_root_only_file(path, field=field))


def generate_bundle_id(now: dt.datetime | None = None) -> str:
    value = now or utc_now()
    return value.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(12)


def artifact_base_key(
    *,
    prefix: str,
    source_site: str,
    destination_site: str,
    release_sha: str,
    bundle_id: str,
) -> str:
    return "/".join(
        (
            prefix,
            "release-artifacts",
            OBJECT_LAYOUT_VERSION,
            source_site,
            destination_site,
            release_sha,
            bundle_id,
        )
    )


def _artifact_key(base: str, name: str) -> str:
    return base + "/artifacts/" + name + ".age"


def _manifest_key(base: str) -> str:
    return base + "/manifest.json.age"


def bootstrap_base_key(
    *,
    prefix: str,
    source_site: str,
    destination_site: str,
    control_release_sha: str,
    bootstrap_id: str,
) -> str:
    """Return the isolated, create-only namespace for the first consumer package."""

    return "/".join(
        (
            prefix,
            "bootstrap-artifacts",
            OBJECT_LAYOUT_VERSION,
            source_site,
            destination_site,
            control_release_sha,
            bootstrap_id,
        )
    )


def _bootstrap_key(base: str) -> str:
    return base + "/stage-consumer-bootstrap.tar.age"


def parse_artifact_specifications(values: Sequence[str]) -> list[ArtifactInput]:
    if not values:
        raise ArtifactStageError("at least one --artifact NAME=ABSOLUTE_PATH is required")
    artifacts: list[ArtifactInput] = []
    names: set[str] = set()
    for value in values:
        if "=" not in value:
            raise ArtifactStageError("artifact must use NAME=ABSOLUTE_PATH")
        name, raw_path = value.split("=", 1)
        name = require_id(name, "artifact name", ARTIFACT_NAME_RE)
        if name in names:
            raise ArtifactStageError("artifact names must be unique")
        names.add(name)
        path = Path(raw_path)
        if not path.is_absolute():
            raise ArtifactStageError("artifact path must be absolute")
        artifacts.append(ArtifactInput(name=name, path=path))
    return sorted(artifacts, key=lambda item: item.name)


def normalize_artifact_bindings(value: Mapping[str, str], *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ArtifactStageError(f"{field} must be a mapping")
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = require_id(raw_key, f"{field} key", BINDING_KEY_RE)
        text = require_string(raw_value, f"{field}.{key}")
        if len(text) > 512 or any(ord(character) < 0x20 or ord(character) == 0x7F for character in text):
            raise ArtifactStageError(f"{field}.{key} has an unsafe format")
        result[key] = text
    return dict(sorted(result.items()))


def apply_artifact_bindings(artifacts: Sequence[ArtifactInput], values: Sequence[str]) -> list[ArtifactInput]:
    """Attach signed non-secret metadata such as an image digest or image tag."""

    by_name = {artifact.name: artifact for artifact in artifacts}
    bindings: dict[str, dict[str, str]] = {
        artifact.name: normalize_artifact_bindings(artifact.bindings, field=f"artifact {artifact.name} bindings")
        for artifact in artifacts
    }
    for value in values:
        parts = value.split("=", 2)
        if len(parts) != 3:
            raise ArtifactStageError("artifact binding must use NAME=KEY=VALUE")
        name = require_id(parts[0], "artifact binding name", ARTIFACT_NAME_RE)
        if name not in by_name:
            raise ArtifactStageError("artifact binding refers to an unknown artifact")
        key = require_id(parts[1], "artifact binding key", BINDING_KEY_RE)
        normalized = normalize_artifact_bindings({key: parts[2]}, field=f"artifact {name} bindings")
        if key in bindings[name]:
            raise ArtifactStageError("artifact binding keys must be unique per artifact")
        bindings[name].update(normalized)
    return [
        ArtifactInput(name=artifact.name, path=artifact.path, bindings=dict(sorted(bindings[artifact.name].items())))
        for artifact in artifacts
    ]


@contextlib.contextmanager
def locked_workspace(workspace: Path, *, name: str) -> Iterator[Path]:
    require_private_workspace(workspace, field="workspace")
    safe_name = require_id(name, "workspace lock name", BUNDLE_ID_RE)
    try:
        with snapshot.exclusive_workspace_lock(workspace, name="artifact-stage-" + safe_name):
            with tempfile.TemporaryDirectory(prefix="artifact-stage-", dir=str(workspace)) as temporary:
                temporary_path = Path(temporary)
                temporary_path.chmod(0o700)
                require_private_workspace(temporary_path, field="artifact workspace")
                yield temporary_path
    except snapshot.SnapshotTransportError as exc:
        raise ArtifactStageError(str(exc)) from exc


def write_atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise ArtifactStageError("refusing to overwrite a local JSON artifact")
    encoded = canonical_json_bytes(payload) + b"\n"
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(8))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(temporary), flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def run_age_encrypt(age_binary: str, recipient: str, input_path: Path, output_path: Path) -> None:
    _snapshot_error(lambda: snapshot.run_age_encrypt(age_binary, recipient, input_path, output_path))


def run_age_decrypt(age_binary: str, identity_file: Path, input_path: Path, output_path: Path) -> None:
    _snapshot_error(lambda: snapshot.run_age_decrypt(age_binary, identity_file, input_path, output_path))


def _metadata_for_ciphertext(ciphertext_sha256: str) -> dict[str, str]:
    return {
        "transport-schema": TRANSPORT_SCHEMA,
        "encryption": OBJECT_ENCRYPTION,
        "ciphertext-sha256": ciphertext_sha256,
    }


def _write_stream_to_new_file(stream: Any, output_path: Path) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(output_path), flags, 0o600)
    except FileExistsError as exc:
        raise ArtifactStageError("refusing to overwrite a local artifact") from exc
    except OSError as exc:
        raise ArtifactStageError("cannot safely create a local artifact") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as handle:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise ArtifactStageError("download returned non-bytes content")
                handle.write(chunk)
                digest.update(chunk)
                total += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), total


def _get_response_metadata(response: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = response.get("Metadata", {})
    if not isinstance(metadata, Mapping):
        raise ArtifactStageError("Object Storage metadata is malformed")
    return metadata


def _verify_exact_object_readback(
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
    try:
        try:
            response = client.get_object(Bucket=bucket, Key=key, VersionId=version_id)
        except Exception as exc:
            raise ArtifactStageError("cannot read back the exact immutable Object Storage version") from exc
        if not isinstance(response, Mapping):
            raise ArtifactStageError("Object Storage read-back response is malformed")
        if require_version_id(response.get("VersionId"), "Object Storage read-back VersionId") != version_id:
            raise ArtifactStageError("Object Storage read-back returned a different VersionId")
        if response.get("ServerSideEncryption"):
            raise ArtifactStageError("provider-side Object Storage encryption is not permitted")
        metadata = _get_response_metadata(response)
        if (
            metadata.get("transport-schema") != TRANSPORT_SCHEMA
            or metadata.get("encryption") != OBJECT_ENCRYPTION
            or metadata.get("ciphertext-sha256") != expected_sha256
        ):
            raise ArtifactStageError("Object Storage read-back metadata does not match the encrypted artifact")
        body = response.get("Body")
        if body is None or not hasattr(body, "read"):
            raise ArtifactStageError("Object Storage read-back has no readable body")
        digest, size = _write_stream_to_new_file(body, target)
        if digest != expected_sha256 or size != expected_bytes:
            raise ArtifactStageError("Object Storage read-back ciphertext does not match the uploaded artifact")
    finally:
        target.unlink(missing_ok=True)


def upload_immutable_encrypted_object(
    client: Any,
    *,
    bucket: str,
    key: str,
    ciphertext_path: Path,
    workspace: Path,
) -> dict[str, Any]:
    _snapshot_error(lambda: snapshot.assert_object_absent(client, bucket=bucket, key=key))
    ciphertext_sha256, ciphertext_bytes = sha256_file(ciphertext_path)
    with ciphertext_path.open("rb") as handle:
        try:
            response = client.put_object(
                Bucket=bucket,
                Key=key,
                Body=handle,
                ContentType="application/octet-stream",
                Metadata=_metadata_for_ciphertext(ciphertext_sha256),
                IfNoneMatch="*",
            )
        except Exception as exc:
            raise ArtifactStageError("conditional immutable Object Storage upload failed") from exc
    if not isinstance(response, Mapping):
        raise ArtifactStageError("Object Storage upload returned a malformed response")
    if response.get("ServerSideEncryption"):
        raise ArtifactStageError("provider-side Object Storage encryption is not permitted")
    version_id = require_version_id(response.get("VersionId"), "Object Storage upload VersionId")
    if version_id == "null":
        raise ArtifactStageError("versioned Object Storage upload did not return a VersionId")
    _snapshot_error(
        lambda: snapshot.require_singleton_immutable_object_version(
            client,
            bucket=bucket,
            key=key,
            expected_version_id=version_id,
        )
    )
    _verify_exact_object_readback(
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


def _require_ed25519_backend() -> None:
    if Ed25519PrivateKey is None or Ed25519PublicKey is None or serialization is None or InvalidSignature is None:
        raise ArtifactStageError("cryptography Ed25519 support is unavailable")


def unsigned_manifest_payload(manifest: Mapping[str, Any]) -> bytes:
    return MANIFEST_SIGNATURE_DOMAIN + canonical_json_bytes(
        {key: value for key, value in manifest.items() if key != "source_signature"}
    )


def _load_private_signing_key(path: Path) -> Any:
    key_path = require_root_only_input(path, field="source_signing_private_key_file", maximum_bytes=256)
    raw = key_path.read_bytes()
    if len(raw) != 32:
        raise ArtifactStageError("source_signing_private_key_file must contain exactly 32 raw Ed25519 bytes")
    _require_ed25519_backend()
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except ValueError as exc:  # pragma: no cover - exact-sized raw key is accepted by cryptography.
        raise ArtifactStageError("source_signing_private_key_file is invalid") from exc


def _publisher_public_key(config: PublisherConfig) -> bytes:
    private_key = _load_private_signing_key(config.source_signing_private_key_file)
    _require_ed25519_backend()
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def sign_manifest(manifest: Mapping[str, Any], *, config: PublisherConfig) -> dict[str, str]:
    private_key = _load_private_signing_key(config.source_signing_private_key_file)
    signature = private_key.sign(unsigned_manifest_payload(manifest))
    _require_ed25519_backend()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "algorithm": MANIFEST_SIGNATURE_ALGORITHM,
        "key_id": sha256_bytes(public_key),
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def verify_manifest_signature(manifest: Mapping[str, Any], *, config: ConsumerConfig) -> None:
    signature_value = manifest.get("source_signature")
    if not isinstance(signature_value, Mapping) or set(signature_value) != {"algorithm", "key_id", "signature"}:
        raise ArtifactStageError("manifest source_signature is unsupported")
    if signature_value.get("algorithm") != MANIFEST_SIGNATURE_ALGORITHM:
        raise ArtifactStageError("manifest source_signature algorithm is unsupported")
    expected_key_id = sha256_bytes(config.source_signing_public_key)
    if require_id(signature_value.get("key_id"), "manifest source_signature.key_id", SHA256_RE) != expected_key_id:
        raise ArtifactStageError("manifest source_signature key_id does not match the pinned source key")
    raw_signature = decode_exact_base64(
        signature_value.get("signature"),
        field="manifest source_signature.signature",
        expected_bytes=64,
    )
    _require_ed25519_backend()
    try:
        public_key = Ed25519PublicKey.from_public_bytes(config.source_signing_public_key)
        public_key.verify(raw_signature, unsigned_manifest_payload(manifest))
    except InvalidSignature as exc:
        raise ArtifactStageError("manifest source_signature verification failed") from exc
    except ValueError as exc:  # pragma: no cover - fixed-length key passed from config parser.
        raise ArtifactStageError("pinned source signing public key is invalid") from exc


def _expected_url_path(bucket: str, object_key: str) -> str:
    return "/" + urllib.parse.quote(bucket, safe="") + "/" + urllib.parse.quote(object_key, safe="/")


def require_version_bound_presigned_url(
    value: object,
    *,
    endpoint: str,
    bucket: str,
    object_key: str,
    version_id: str,
) -> str:
    url = require_string(value, "presigned URL")
    if len(url) > 8192:
        raise ArtifactStageError("presigned URL is too long")
    parsed = urllib.parse.urlparse(url)
    endpoint_parsed = urllib.parse.urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.hostname != endpoint_parsed.hostname
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.path != _expected_url_path(bucket, object_key)
    ):
        raise ArtifactStageError("presigned URL is not bound to the configured private Object Storage endpoint")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
    versions = query.get("versionId")
    if versions != [version_id]:
        raise ArtifactStageError("presigned URL must bind exactly one matching VersionId")
    sigv4 = all(
        len(query.get(name, [])) == 1 and bool(query[name][0])
        for name in ("X-Amz-Algorithm", "X-Amz-Credential", "X-Amz-Signature")
    )
    sigv2 = all(
        len(query.get(name, [])) == 1 and bool(query[name][0])
        for name in ("AWSAccessKeyId", "Signature", "Expires")
    )
    if not sigv4 and not sigv2:
        raise ArtifactStageError("presigned URL does not contain a supported signed-request envelope")
    return url


def create_version_bound_presigned_url(
    client: Any,
    *,
    config: PublisherConfig,
    object_key: str,
    version_id: str,
) -> str:
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": config.bucket, "Key": object_key, "VersionId": version_id},
            ExpiresIn=config.presign_expires_seconds,
            HttpMethod="GET",
        )
    except Exception as exc:
        raise ArtifactStageError("cannot create a version-bound presigned Object Storage URL") from exc
    return require_version_bound_presigned_url(
        url,
        endpoint=config.endpoint,
        bucket=config.bucket,
        object_key=object_key,
        version_id=version_id,
    )


def _artifact_descriptor(
    *,
    name: str,
    plaintext_sha256: str,
    plaintext_bytes: int,
    remote: Mapping[str, Any],
    download_url: str,
    bindings: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "name": name,
        "sha256": plaintext_sha256,
        "bytes": plaintext_bytes,
        "object_key": remote["object_key"],
        "version_id": remote["version_id"],
        "ciphertext_sha256": remote["ciphertext_sha256"],
        "ciphertext_bytes": remote["ciphertext_bytes"],
        "download_url": download_url,
        "bindings": normalize_artifact_bindings(bindings, field=f"artifact {name} bindings"),
    }


def publish_bundle(
    client: Any,
    *,
    config: PublisherConfig,
    destination_site: str,
    release_sha: str,
    artifacts: Sequence[ArtifactInput],
    bundle_id: str | None = None,
    now: dt.datetime | None = None,
    encryptor: Callable[[str, str, Path, Path], None] = run_age_encrypt,
) -> dict[str, Any]:
    destination_site = require_id(destination_site, "destination_site", SITE_RE)
    if destination_site == config.source_site:
        raise ArtifactStageError("source_site and destination_site must differ")
    release_sha = require_id(release_sha, "release_sha", RELEASE_SHA_RE)
    bundle_id = require_id(bundle_id or generate_bundle_id(now), "bundle_id", BUNDLE_ID_RE)
    artifact_inputs = list(artifacts)
    if not artifact_inputs:
        raise ArtifactStageError("at least one artifact is required")
    if artifact_inputs != sorted(artifact_inputs, key=lambda item: item.name):
        artifact_inputs = sorted(artifact_inputs, key=lambda item: item.name)
    if len({item.name for item in artifact_inputs}) != len(artifact_inputs):
        raise ArtifactStageError("artifact names must be unique")
    for artifact in artifact_inputs:
        require_id(artifact.name, "artifact name", ARTIFACT_NAME_RE)
        require_root_only_input(
            artifact.path,
            field=f"artifact {artifact.name}",
            maximum_bytes=config.maximum_artifact_bytes,
        )
    _load_private_signing_key(config.source_signing_private_key_file)
    _snapshot_error(lambda: snapshot.assert_private_versioned_bucket(client, config.bucket))
    base = artifact_base_key(
        prefix=config.prefix,
        source_site=config.source_site,
        destination_site=destination_site,
        release_sha=release_sha,
        bundle_id=bundle_id,
    )
    with locked_workspace(config.workspace, name="publish-" + bundle_id) as workspace:
        pending_descriptors: list[tuple[ArtifactInput, str, int, dict[str, Any]]] = []
        for artifact in artifact_inputs:
            plaintext_sha256, plaintext_bytes = sha256_file(artifact.path)
            ciphertext = workspace / (artifact.name + ".age")
            encryptor(config.age_binary, config.age_recipient, artifact.path, ciphertext)
            require_private_file(ciphertext, field=f"encrypted artifact {artifact.name}")
            # Bind the signed descriptor to the exact plaintext actually passed to age.
            if sha256_file(artifact.path) != (plaintext_sha256, plaintext_bytes):
                raise ArtifactStageError("artifact changed while it was being encrypted")
            remote = upload_immutable_encrypted_object(
                client,
                bucket=config.bucket,
                key=_artifact_key(base, artifact.name),
                ciphertext_path=ciphertext,
                workspace=workspace,
            )
            pending_descriptors.append((artifact, plaintext_sha256, plaintext_bytes, remote))
        # Issue all short-lived URLs only after every (possibly large) artifact has been read back.
        descriptors = [
            _artifact_descriptor(
                name=artifact.name,
                plaintext_sha256=plaintext_sha256,
                plaintext_bytes=plaintext_bytes,
                remote=remote,
                download_url=create_version_bound_presigned_url(
                    client,
                    config=config,
                    object_key=remote["object_key"],
                    version_id=remote["version_id"],
                ),
                bindings=artifact.bindings,
            )
            for artifact, plaintext_sha256, plaintext_bytes, remote in pending_descriptors
        ]
        manifest: dict[str, Any] = {
            "schema": MANIFEST_SCHEMA,
            "status": "committed",
            "source_site": config.source_site,
            "destination_site": destination_site,
            "release_sha": release_sha,
            "bundle_id": bundle_id,
            "published_at": utc_iso(now or utc_now()),
            "artifacts": descriptors,
        }
        manifest["source_signature"] = sign_manifest(manifest, config=config)
        plaintext_manifest = workspace / "manifest.json"
        write_atomic_json(plaintext_manifest, manifest)
        manifest_ciphertext = workspace / "manifest.json.age"
        encryptor(config.age_binary, config.age_recipient, plaintext_manifest, manifest_ciphertext)
        # The encrypted manifest carries short-lived artifact URLs; keep its plaintext only long enough to age-encrypt it.
        plaintext_manifest.unlink()
        require_private_file(manifest_ciphertext, field="encrypted manifest")
        manifest_sha256, manifest_bytes = sha256_file(manifest_ciphertext)
        if manifest_bytes > MAXIMUM_MANIFEST_CIPHERTEXT_BYTES:
            raise ArtifactStageError("encrypted manifest exceeds its fixed size bound")
        manifest_remote = upload_immutable_encrypted_object(
            client,
            bucket=config.bucket,
            key=_manifest_key(base),
            ciphertext_path=manifest_ciphertext,
            workspace=workspace,
        )
        manifest_url = create_version_bound_presigned_url(
            client,
            config=config,
            object_key=manifest_remote["object_key"],
            version_id=manifest_remote["version_id"],
        )
    return {
        "schema": PUBLISH_RECEIPT_SCHEMA,
        "status": "published",
        "source_site": config.source_site,
        "destination_site": destination_site,
        "release_sha": release_sha,
        "bundle_id": bundle_id,
        "published_at": manifest["published_at"],
        "artifacts": [
            {key: value for key, value in descriptor.items() if key != "download_url"}
            for descriptor in descriptors
        ],
        "manifest": {
            **manifest_remote,
            "ciphertext_sha256": manifest_sha256,
            "ciphertext_bytes": manifest_bytes,
            "presigned_url": manifest_url,
        },
    }


def publish_bootstrap_package(
    client: Any,
    *,
    config: PublisherConfig,
    bootstrap_package_directory: Path,
    bootstrap_preparation_receipt: Path,
    bootstrap_id: str | None = None,
    now: dt.datetime | None = None,
    encryptor: Callable[[str, str, Path, Path], None] = run_age_encrypt,
) -> dict[str, Any]:
    """Publish exactly one encrypted consumer bootstrap before any stage manifest exists.

    The regular ``publish`` command intentionally returns only the encrypted
    manifest URL, because artifact URLs belong inside its signed manifest.
    WA-IR has no consumer before the first delivery, so its minimal consumer
    package needs this separate one-object bootstrap path.  The caller must
    present the root-only package directory and its canonical preparation
    receipt, then use the returned short-lived version-bound URL only as a
    transient SSH control argument.  This helper never writes that URL to disk.
    """

    bootstrap = _load_bootstrap_preparation_primitives()
    if config.source_site != bootstrap.WA_IR_BOOTSTRAP_SOURCE_SITE:
        raise ArtifactStageError("bootstrap publisher source_site must be webapp_fi")
    if config.age_recipient != bootstrap.WA_IR_BOOTSTRAP_AGE_RECIPIENT:
        raise ArtifactStageError("bootstrap publisher age_recipient is not the fixed WA-IR recipient")
    try:
        prepared = bootstrap.verify_prepared_bootstrap_package(
            package_directory=bootstrap_package_directory,
            preparation_receipt=bootstrap_preparation_receipt,
        )
    except bootstrap.BootstrapPreparationError as exc:
        raise ArtifactStageError(f"bootstrap preparation verification failed: {exc}") from exc
    consumer_config = prepared["consumer_config"]
    if consumer_config.get("source_site") != bootstrap.WA_IR_BOOTSTRAP_SOURCE_SITE:
        raise ArtifactStageError("bootstrap consumer config source_site must be webapp_fi")
    if consumer_config.get("age_identity_file") != bootstrap.WA_IR_BOOTSTRAP_IDENTITY_FILE:
        raise ArtifactStageError("bootstrap consumer config does not use the fixed WA-IR identity path")
    if any(
        consumer_config[field] != getattr(config, field)
        for field in ("endpoint", "region", "bucket", "prefix")
    ):
        raise ArtifactStageError("bootstrap consumer transport config does not match the publisher config")
    try:
        configured_public_key = base64.b64decode(
            str(consumer_config["source_signing_public_key_base64"]), validate=True
        )
    except (KeyError, ValueError, binascii.Error) as exc:  # pragma: no cover - preparation verifies this schema.
        raise ArtifactStageError("bootstrap consumer config source signing key is invalid") from exc
    if _publisher_public_key(config) != configured_public_key:
        raise ArtifactStageError("bootstrap consumer config source signing key does not match the publisher key")
    control_release_sha = prepared["control_commit"]
    if not bootstrap.COMMIT_RE.fullmatch(control_release_sha):  # pragma: no cover - preparation verifies this contract.
        raise ArtifactStageError("bootstrap control commit must be exactly 40 lowercase hexadecimal characters")
    bootstrap_path = Path(prepared["archive_path"])
    if prepared["archive_bytes"] > bootstrap.MAX_ARCHIVE_BYTES:  # pragma: no cover - preparation verifies this contract.
        raise ArtifactStageError("bootstrap archive exceeds the fixed 8 MiB maximum")
    bootstrap_id = require_id(
        bootstrap_id or generate_bundle_id(now),
        "bootstrap_id",
        BUNDLE_ID_RE,
    )
    _snapshot_error(lambda: snapshot.assert_private_versioned_bucket(client, config.bucket))
    base = bootstrap_base_key(
        prefix=config.prefix,
        source_site=bootstrap.WA_IR_BOOTSTRAP_SOURCE_SITE,
        destination_site=bootstrap.WA_IR_BOOTSTRAP_DESTINATION_SITE,
        control_release_sha=control_release_sha,
        bootstrap_id=bootstrap_id,
    )
    with locked_workspace(config.workspace, name="bootstrap-" + bootstrap_id) as workspace:
        plaintext_sha256, plaintext_bytes = sha256_file(bootstrap_path)
        if (plaintext_sha256, plaintext_bytes) != (prepared["archive_sha256"], prepared["archive_bytes"]):
            raise ArtifactStageError("bootstrap archive changed after preparation verification")
        ciphertext = workspace / "stage-consumer-bootstrap.tar.age"
        encryptor(config.age_binary, config.age_recipient, bootstrap_path, ciphertext)
        require_private_file(ciphertext, field="encrypted stage consumer bootstrap package")
        if sha256_file(bootstrap_path) != (prepared["archive_sha256"], prepared["archive_bytes"]):
            raise ArtifactStageError("stage consumer bootstrap package changed while being encrypted")
        remote = upload_immutable_encrypted_object(
            client,
            bucket=config.bucket,
            key=_bootstrap_key(base),
            ciphertext_path=ciphertext,
            workspace=workspace,
        )
        presigned_url = create_version_bound_presigned_url(
            client,
            config=config,
            object_key=remote["object_key"],
            version_id=remote["version_id"],
        )
    return {
        "schema": BOOTSTRAP_PUBLISH_RECEIPT_SCHEMA,
        "status": "published",
        "source_site": bootstrap.WA_IR_BOOTSTRAP_SOURCE_SITE,
        "destination_site": bootstrap.WA_IR_BOOTSTRAP_DESTINATION_SITE,
        "control_commit": control_release_sha,
        "control_tree": prepared["control_tree"],
        "bootstrap_id": bootstrap_id,
        "published_at": utc_iso(now or utc_now()),
        "bootstrap": {
            **remote,
            "plaintext_sha256": plaintext_sha256,
            "plaintext_bytes": plaintext_bytes,
            "manifest_sha256": prepared["package_manifest_sha256"],
            "preparation_receipt_sha256": prepared["preparation_receipt_sha256"],
            "presigned_url": presigned_url,
        },
    }


def _header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    if hasattr(headers, "get"):
        value = headers.get(name)
        if value is not None:
            return str(value)
    if isinstance(headers, Mapping):
        target = name.lower()
        for key, value in headers.items():
            if isinstance(key, str) and key.lower() == target:
                return str(value)
    return None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def open_presigned_request(request: urllib.request.Request, timeout: int) -> Any:
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout)


def download_presigned_object(
    *,
    url: str,
    config: ConsumerConfig,
    object_key: str,
    version_id: str,
    expected_sha256: str,
    expected_bytes: int,
    output_path: Path,
    downloader: Callable[[urllib.request.Request, int], Any] = open_presigned_request,
) -> None:
    require_version_bound_presigned_url(
        url,
        endpoint=config.endpoint,
        bucket=config.bucket,
        object_key=object_key,
        version_id=version_id,
    )
    if not SHA256_RE.fullmatch(expected_sha256):
        raise ArtifactStageError("expected ciphertext SHA-256 is invalid")
    if expected_bytes < 1:
        raise ArtifactStageError("expected ciphertext bytes must be positive")
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/octet-stream", "User-Agent": "gold-trade-wa-ir-artifact-stage/1"},
        method="GET",
    )
    try:
        response = downloader(request, DOWNLOAD_TIMEOUT_SECONDS)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise ArtifactStageError("cannot download the exact presigned Object Storage version") from exc
    try:
        status = response.getcode() if hasattr(response, "getcode") else getattr(response, "status", None)
        if status != 200:
            raise ArtifactStageError("presigned Object Storage download did not return HTTP 200")
        if hasattr(response, "geturl") and response.geturl() != url:
            raise ArtifactStageError("presigned Object Storage download redirected")
        headers = getattr(response, "headers", None)
        if _header(headers, "x-amz-version-id") != version_id:
            raise ArtifactStageError("presigned Object Storage download returned a different VersionId")
        if _header(headers, "x-amz-server-side-encryption"):
            raise ArtifactStageError("provider-side Object Storage encryption is not permitted")
        if (
            _header(headers, "x-amz-meta-transport-schema") != TRANSPORT_SCHEMA
            or _header(headers, "x-amz-meta-encryption") != OBJECT_ENCRYPTION
            or _header(headers, "x-amz-meta-ciphertext-sha256") != expected_sha256
        ):
            raise ArtifactStageError("presigned Object Storage metadata does not match the encrypted artifact")
        content_length = _header(headers, "content-length")
        if content_length is not None:
            try:
                if int(content_length, 10) != expected_bytes:
                    raise ArtifactStageError("presigned Object Storage content length does not match the manifest")
            except ValueError as exc:
                raise ArtifactStageError("presigned Object Storage content length is invalid") from exc
        digest, size = _write_stream_to_new_file(response, output_path)
        if digest != expected_sha256 or size != expected_bytes:
            raise ArtifactStageError("presigned Object Storage ciphertext does not match the manifest")
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _parse_utc_iso(value: object, field: str) -> str:
    text = require_string(value, field)
    if not text.endswith("Z"):
        raise ArtifactStageError(f"{field} must be an RFC3339 UTC timestamp")
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArtifactStageError(f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise ArtifactStageError(f"{field} must include a timezone")
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_artifact_descriptor(
    value: object,
    *,
    config: ConsumerConfig,
    base: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ArtifactStageError("manifest artifact must be an object")
    descriptor = dict(value)
    expected_fields = {
        "name",
        "sha256",
        "bytes",
        "object_key",
        "version_id",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "download_url",
        "bindings",
    }
    if set(descriptor) != expected_fields:
        raise ArtifactStageError("manifest artifact fields are unsupported")
    name = require_id(descriptor.get("name"), "manifest artifact name", ARTIFACT_NAME_RE)
    plaintext_sha = require_id(descriptor.get("sha256"), "manifest artifact sha256", SHA256_RE)
    plaintext_bytes = require_positive_int(
        descriptor.get("bytes"),
        "manifest artifact bytes",
        maximum=config.maximum_artifact_bytes,
    )
    object_key = require_string(descriptor.get("object_key"), "manifest artifact object_key")
    expected_key = _artifact_key(base, name)
    if object_key != expected_key:
        raise ArtifactStageError("manifest artifact object key does not match its immutable bundle location")
    version_id = require_version_id(descriptor.get("version_id"), "manifest artifact version_id")
    ciphertext_sha = require_id(descriptor.get("ciphertext_sha256"), "manifest artifact ciphertext_sha256", SHA256_RE)
    ciphertext_bytes = require_positive_int(
        descriptor.get("ciphertext_bytes"),
        "manifest artifact ciphertext_bytes",
        maximum=config.maximum_artifact_bytes + 1024 * 1024,
    )
    download_url = require_version_bound_presigned_url(
        descriptor.get("download_url"),
        endpoint=config.endpoint,
        bucket=config.bucket,
        object_key=object_key,
        version_id=version_id,
    )
    bindings_value = descriptor.get("bindings")
    if not isinstance(bindings_value, Mapping):
        raise ArtifactStageError("manifest artifact bindings must be an object")
    bindings = normalize_artifact_bindings(bindings_value, field=f"manifest artifact {name} bindings")
    return {
        "name": name,
        "sha256": plaintext_sha,
        "bytes": plaintext_bytes,
        "object_key": object_key,
        "version_id": version_id,
        "ciphertext_sha256": ciphertext_sha,
        "ciphertext_bytes": ciphertext_bytes,
        "download_url": download_url,
        "bindings": bindings,
    }


def validate_manifest(
    value: object,
    *,
    config: ConsumerConfig,
    destination_site: str,
    release_sha: str,
    bundle_id: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ArtifactStageError("decrypted manifest must be a JSON object")
    manifest = dict(value)
    expected_fields = {
        "schema",
        "status",
        "source_site",
        "destination_site",
        "release_sha",
        "bundle_id",
        "published_at",
        "artifacts",
        "source_signature",
    }
    if set(manifest) != expected_fields:
        raise ArtifactStageError("manifest fields are unsupported")
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("status") != "committed":
        raise ArtifactStageError("manifest schema or status is unsupported")
    source_site = require_id(manifest.get("source_site"), "manifest source_site", SITE_RE)
    actual_destination = require_id(manifest.get("destination_site"), "manifest destination_site", SITE_RE)
    actual_release = require_id(manifest.get("release_sha"), "manifest release_sha", RELEASE_SHA_RE)
    actual_bundle = require_id(manifest.get("bundle_id"), "manifest bundle_id", BUNDLE_ID_RE)
    if (
        source_site != config.source_site
        or actual_destination != destination_site
        or actual_release != release_sha
        or actual_bundle != bundle_id
    ):
        raise ArtifactStageError("manifest binding does not match this detached staging request")
    verify_manifest_signature(manifest, config=config)
    published_at = _parse_utc_iso(manifest.get("published_at"), "manifest published_at")
    artifacts_value = manifest.get("artifacts")
    if not isinstance(artifacts_value, list) or not artifacts_value:
        raise ArtifactStageError("manifest must contain at least one artifact")
    base = artifact_base_key(
        prefix=config.prefix,
        source_site=source_site,
        destination_site=actual_destination,
        release_sha=actual_release,
        bundle_id=actual_bundle,
    )
    artifacts = [_validate_artifact_descriptor(item, config=config, base=base) for item in artifacts_value]
    names = [item["name"] for item in artifacts]
    if names != sorted(names) or len(set(names)) != len(names):
        raise ArtifactStageError("manifest artifacts must be uniquely sorted by name")
    return {
        "schema": MANIFEST_SCHEMA,
        "status": "committed",
        "source_site": source_site,
        "destination_site": actual_destination,
        "release_sha": actual_release,
        "bundle_id": actual_bundle,
        "published_at": published_at,
        "artifacts": artifacts,
    }


def _candidate_directory(
    staging_root: Path,
    *,
    source_site: str,
    release_sha: str,
    bundle_id: str,
) -> Path:
    if not staging_root.is_absolute():
        raise ArtifactStageError("staging_root must be an absolute path")
    require_private_workspace(staging_root, field="staging_root")
    parent = staging_root / source_site / release_sha
    require_private_workspace(parent, field="detached staging parent")
    return parent / bundle_id


def _manifest_from_encrypted_file(
    *,
    config: ConsumerConfig,
    encrypted_manifest: Path,
    plaintext_manifest: Path,
    decryptor: Callable[[str, Path, Path, Path], None],
) -> dict[str, Any]:
    decryptor(config.age_binary, config.age_identity_file, encrypted_manifest, plaintext_manifest)
    require_private_file(plaintext_manifest, field="decrypted manifest")
    try:
        value = json.loads(plaintext_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactStageError("decrypted manifest is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ArtifactStageError("decrypted manifest must be a JSON object")
    return value


def stage_bundle(
    *,
    config: ConsumerConfig,
    destination_site: str,
    release_sha: str,
    bundle_id: str,
    manifest_url: str,
    manifest_version_id: str,
    manifest_ciphertext_sha256: str,
    manifest_ciphertext_bytes: int,
    staging_root: Path,
    now: dt.datetime | None = None,
    downloader: Callable[[urllib.request.Request, int], Any] = open_presigned_request,
    decryptor: Callable[[str, Path, Path, Path], None] = run_age_decrypt,
) -> dict[str, Any]:
    destination_site = require_id(destination_site, "destination_site", SITE_RE)
    if destination_site == config.source_site:
        raise ArtifactStageError("source_site and destination_site must differ")
    release_sha = require_id(release_sha, "release_sha", RELEASE_SHA_RE)
    bundle_id = require_id(bundle_id, "bundle_id", BUNDLE_ID_RE)
    manifest_version_id = require_version_id(manifest_version_id, "manifest_version_id")
    manifest_ciphertext_sha256 = require_id(
        manifest_ciphertext_sha256,
        "manifest_ciphertext_sha256",
        SHA256_RE,
    )
    manifest_ciphertext_bytes = require_positive_int(
        manifest_ciphertext_bytes,
        "manifest_ciphertext_bytes",
        maximum=MAXIMUM_MANIFEST_CIPHERTEXT_BYTES,
    )
    base = artifact_base_key(
        prefix=config.prefix,
        source_site=config.source_site,
        destination_site=destination_site,
        release_sha=release_sha,
        bundle_id=bundle_id,
    )
    manifest_key = _manifest_key(base)
    require_version_bound_presigned_url(
        manifest_url,
        endpoint=config.endpoint,
        bucket=config.bucket,
        object_key=manifest_key,
        version_id=manifest_version_id,
    )
    candidate = _candidate_directory(
        staging_root,
        source_site=config.source_site,
        release_sha=release_sha,
        bundle_id=bundle_id,
    )
    if candidate.exists() or candidate.is_symlink():
        raise ArtifactStageError("refusing to overwrite an existing detached staging candidate")
    with locked_workspace(config.workspace, name="consume-" + bundle_id) as workspace:
        encrypted_manifest = workspace / "manifest.json.age"
        plaintext_manifest = workspace / "manifest.json"
        download_presigned_object(
            url=manifest_url,
            config=config,
            object_key=manifest_key,
            version_id=manifest_version_id,
            expected_sha256=manifest_ciphertext_sha256,
            expected_bytes=manifest_ciphertext_bytes,
            output_path=encrypted_manifest,
            downloader=downloader,
        )
        raw_manifest = _manifest_from_encrypted_file(
            config=config,
            encrypted_manifest=encrypted_manifest,
            plaintext_manifest=plaintext_manifest,
            decryptor=decryptor,
        )
        manifest = validate_manifest(
            raw_manifest,
            config=config,
            destination_site=destination_site,
            release_sha=release_sha,
            bundle_id=bundle_id,
        )
        # Do not retain the decrypted short-lived artifact URLs in the staging candidate or workspace beyond validation.
        plaintext_manifest.unlink(missing_ok=True)
        incoming = candidate.with_name(".incoming-" + bundle_id + "-" + secrets.token_hex(8))
        incoming.mkdir(mode=0o700)
        try:
            staged_artifacts: list[dict[str, Any]] = []
            for descriptor in manifest["artifacts"]:
                ciphertext = workspace / ("artifact-" + descriptor["name"] + ".age")
                plaintext = incoming / descriptor["name"]
                download_presigned_object(
                    url=descriptor["download_url"],
                    config=config,
                    object_key=descriptor["object_key"],
                    version_id=descriptor["version_id"],
                    expected_sha256=descriptor["ciphertext_sha256"],
                    expected_bytes=descriptor["ciphertext_bytes"],
                    output_path=ciphertext,
                    downloader=downloader,
                )
                decryptor(config.age_binary, config.age_identity_file, ciphertext, plaintext)
                require_private_file(plaintext, field=f"staged artifact {descriptor['name']}")
                actual_sha256, actual_bytes = sha256_file(plaintext)
                if actual_sha256 != descriptor["sha256"] or actual_bytes != descriptor["bytes"]:
                    raise ArtifactStageError("decrypted artifact does not match the signed manifest")
                staged_artifacts.append(
                    {
                        key: value
                        for key, value in descriptor.items()
                        if key != "download_url"
                    }
                )
            receipt: dict[str, Any] = {
                "schema": STAGE_RECEIPT_SCHEMA,
                "status": "staged",
                "source_site": manifest["source_site"],
                "destination_site": manifest["destination_site"],
                "release_sha": manifest["release_sha"],
                "bundle_id": manifest["bundle_id"],
                "published_at": manifest["published_at"],
                "staged_at": utc_iso(now or utc_now()),
                "candidate_directory": str(candidate),
                "manifest": {
                    "object_key": manifest_key,
                    "version_id": manifest_version_id,
                    "ciphertext_sha256": manifest_ciphertext_sha256,
                    "ciphertext_bytes": manifest_ciphertext_bytes,
                },
                "artifacts": staged_artifacts,
            }
            receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
            write_atomic_json(incoming / "stage-receipt.json", receipt)
            if candidate.exists() or candidate.is_symlink():
                raise ArtifactStageError("refusing to overwrite an existing detached staging candidate")
            os.replace(incoming, candidate)
            return receipt
        except Exception:
            shutil.rmtree(incoming, ignore_errors=True)
            raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish = subparsers.add_parser("publish", help="publish a new immutable encrypted artifact bundle")
    publish.add_argument("--config", required=True, type=Path)
    publish.add_argument("--destination-site", required=True)
    publish.add_argument("--release-sha", required=True)
    publish.add_argument("--bundle-id", default=None)
    publish.add_argument("--artifact", action="append", default=[], metavar="NAME=ABSOLUTE_PATH")
    publish.add_argument(
        "--artifact-binding",
        action="append",
        default=[],
        metavar="NAME=KEY=VALUE",
        help="signed non-secret artifact metadata, for example image-bundle=repo_digest=...",
    )

    bootstrap = subparsers.add_parser(
        "publish-bootstrap",
        help="publish exactly one encrypted WA-IR stage-consumer bootstrap package",
    )
    bootstrap.add_argument("--config", required=True, type=Path)
    bootstrap.add_argument("--bootstrap-package-directory", required=True, type=Path)
    bootstrap.add_argument("--bootstrap-preparation-receipt", required=True, type=Path)
    bootstrap.add_argument("--bootstrap-id", default=None)

    consume = subparsers.add_parser("consume", help="stage one exact encrypted artifact bundle via presigned URLs")
    consume.add_argument("--config", required=True, type=Path)
    consume.add_argument("--destination-site", required=True)
    consume.add_argument("--release-sha", required=True)
    consume.add_argument("--bundle-id", required=True)
    consume.add_argument("--manifest-url", required=True)
    consume.add_argument("--manifest-version-id", required=True)
    consume.add_argument("--manifest-ciphertext-sha256", required=True)
    consume.add_argument("--manifest-ciphertext-bytes", required=True, type=int)
    consume.add_argument("--staging-root", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "publish":
            config = load_publisher_config(args.config)
            result = publish_bundle(
                create_s3_client(config),
                config=config,
                destination_site=args.destination_site,
                release_sha=args.release_sha,
                artifacts=apply_artifact_bindings(
                    parse_artifact_specifications(args.artifact),
                    args.artifact_binding,
                ),
                bundle_id=args.bundle_id,
            )
        elif args.command == "publish-bootstrap":
            config = load_publisher_config(args.config)
            result = publish_bootstrap_package(
                create_s3_client(config),
                config=config,
                bootstrap_package_directory=args.bootstrap_package_directory,
                bootstrap_preparation_receipt=args.bootstrap_preparation_receipt,
                bootstrap_id=args.bootstrap_id,
            )
        elif args.command == "consume":
            config = load_consumer_config(args.config)
            result = stage_bundle(
                config=config,
                destination_site=args.destination_site,
                release_sha=args.release_sha,
                bundle_id=args.bundle_id,
                manifest_url=args.manifest_url,
                manifest_version_id=args.manifest_version_id,
                manifest_ciphertext_sha256=args.manifest_ciphertext_sha256,
                manifest_ciphertext_bytes=args.manifest_ciphertext_bytes,
                staging_root=args.staging_root,
            )
        else:  # pragma: no cover - argparse makes this unreachable.
            raise ArtifactStageError("unsupported command")
    except (ArtifactStageError, snapshot.SnapshotTransportError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
