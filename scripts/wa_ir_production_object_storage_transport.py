"""Fail-closed primitives for production WA-IR Object Storage delivery.

This module deliberately has no CLI and performs no work at import time.  A
later, separately reviewed publisher may use it to encrypt one owner-controlled
file, create one private object, verify that exact object version, and mint one
short-lived exact-version download URL.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

from core.secure_file_io import (
    SecureFileError,
    read_secure_text,
    sha256_secure_file,
    write_secure_atomic_bytes,
    write_secure_new_bytes,
)
from scripts.wa_ir_production_transport_contract import (
    AGE_EXECUTABLE,
    ARTIFACT_KIND_RE as _ARTIFACT_KIND_RE,
    ARVAN_ENDPOINT,
    ARVAN_HOST,
    ARVAN_REGION,
    MAX_PAYLOAD_BYTES,
    MAX_READBACK_BYTES,
    PRODUCTION_BUCKET,
    ProductionTransportError,
    SHA256_RE as _SHA256_RE,
    TRANSPORT_SCHEMA,
    validate_object_key_binding,
    validate_operation_id as _validate_operation_id,
    validate_prefix as _validate_prefix,
)


MAX_CONFIG_BYTES = 16 * 1024
MAX_RECIPIENT_BYTES = 4096
MIN_URL_TTL_SECONDS = 60
MAX_URL_TTL_SECONDS = 900
PUBLICATION_JOURNAL_SCHEMA = "wa-ir-production-publication-journal-v1"
MAX_JOURNAL_BYTES = 128 * 1024

_CONFIG_FIELDS = frozenset(
    {
        "ARVAN_S3_ACCESS_KEY",
        "ARVAN_S3_SECRET_KEY",
        "ARVAN_S3_ENDPOINT",
        "ARVAN_S3_REGION",
    }
)
_AGE_RECIPIENT_RE = re.compile(r"^age1[0-9a-z]{20,100}$")
_METADATA_KEY_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
_RESERVED_METADATA = frozenset(
    {
        "artifact-kind",
        "ciphertext-sha256",
        "operation-id",
        "plaintext-sha256",
        "transport-schema",
    }
)


@dataclass(frozen=True)
class ArvanCredentials:
    """Validated Arvan credentials whose representation never exposes secrets."""

    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    endpoint: str
    region: str


@dataclass(frozen=True)
class PublishedObject:
    """Durable, non-secret evidence for one verified encrypted object."""

    bucket: str
    object_key: str
    version_id: str
    plaintext_sha256: str
    plaintext_bytes: int
    ciphertext_sha256: str
    ciphertext_bytes: int
    metadata: Mapping[str, str]

    def evidence(self) -> dict[str, Any]:
        """Return durable evidence.  Presigned URLs are intentionally absent."""

        return {
            "bucket": self.bucket,
            "object_key": self.object_key,
            "version_id": self.version_id,
            "plaintext_sha256": self.plaintext_sha256,
            "plaintext_bytes": self.plaintext_bytes,
            "ciphertext_sha256": self.ciphertext_sha256,
            "ciphertext_bytes": self.ciphertext_bytes,
            "metadata": dict(self.metadata),
            "presigned_url_persisted": False,
        }


class EphemeralPresignedGet:
    """An exact-version URL with redacted string and repr representations."""

    __slots__ = ("__url", "expires_in_seconds", "object_key", "version_id")

    def __init__(
        self,
        url: str,
        *,
        expires_in_seconds: int,
        object_key: str,
        version_id: str,
    ) -> None:
        self.__url = url
        self.expires_in_seconds = expires_in_seconds
        self.object_key = object_key
        self.version_id = version_id

    def reveal_for_control_channel(self) -> str:
        """Reveal the URL only at the bounded, non-durable control boundary."""

        return self.__url

    def __repr__(self) -> str:
        return (
            "EphemeralPresignedGet("
            f"object_key={self.object_key!r}, version_id={self.version_id!r}, "
            f"expires_in_seconds={self.expires_in_seconds}, url=<redacted>)"
        )

    __str__ = __repr__


def _parse_strict_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ProductionTransportError(
                f"production Arvan config line {number} is not KEY=VALUE"
            )
        raw_key, raw_value = line.split("=", 1)
        key = raw_key.strip()
        value = raw_value.strip()
        if not key or key in values or value != raw_value:
            raise ProductionTransportError(
                f"production Arvan config line {number} is ambiguous"
            )
        values[key] = value
    if set(values) != _CONFIG_FIELDS:
        raise ProductionTransportError("production Arvan config fields are invalid")
    return values


def load_secure_credentials(path: Path) -> ArvanCredentials:
    """Read one owner-only credential file and pin it to the approved provider."""

    try:
        values = _parse_strict_env(
            read_secure_text(
                path,
                label="production Arvan Object Storage credentials",
                max_size=MAX_CONFIG_BYTES,
            )
        )
    except ProductionTransportError:
        raise
    except (OSError, SecureFileError) as exc:
        raise ProductionTransportError(
            "production Arvan Object Storage credentials are unavailable or unsafe"
        ) from exc

    access_key = values["ARVAN_S3_ACCESS_KEY"]
    secret_key = values["ARVAN_S3_SECRET_KEY"]
    endpoint = values["ARVAN_S3_ENDPOINT"].rstrip("/")
    region = values["ARVAN_S3_REGION"]
    if endpoint != ARVAN_ENDPOINT or region != ARVAN_REGION:
        raise ProductionTransportError("production Arvan endpoint or region drifted")
    if (
        not 8 <= len(access_key) <= 128
        or not 32 <= len(secret_key) <= 256
        or any(character.isspace() for character in access_key + secret_key)
    ):
        raise ProductionTransportError("production Arvan credentials are malformed")
    return ArvanCredentials(
        access_key=access_key,
        secret_key=secret_key,
        endpoint=endpoint,
        region=region,
    )


def build_client(credentials: ArvanCredentials):  # noqa: ANN201
    """Build the pinned S3-compatible client without consulting ambient config."""

    if credentials.endpoint != ARVAN_ENDPOINT or credentials.region != ARVAN_REGION:
        raise ProductionTransportError("production Arvan endpoint or region drifted")
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise ProductionTransportError(
            "boto3 is unavailable for the controller-side publisher"
        ) from exc
    return boto3.client(
        "s3",
        endpoint_url=credentials.endpoint,
        region_name=credentials.region,
        aws_access_key_id=credentials.access_key,
        aws_secret_access_key=credentials.secret_key,
        config=Config(
            signature_version="s3v4",
            connect_timeout=10,
            read_timeout=120,
            retries={"mode": "standard", "max_attempts": 3},
            s3={"addressing_style": "path"},
        ),
    )


def _provider_error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return ""
    error = response.get("Error")
    return str(error.get("Code") or "") if isinstance(error, dict) else ""


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _policy_value_has_wildcard(value: Any) -> bool:
    if isinstance(value, str):
        return "*" in value
    if isinstance(value, list):
        return any(_policy_value_has_wildcard(item) for item in value)
    if isinstance(value, dict):
        return any(_policy_value_has_wildcard(item) for item in value.values())
    return False


def _policy_action_allows_public_read(value: Any) -> bool:
    actions = [value] if isinstance(value, str) else value
    if not isinstance(actions, list) or not actions:
        return False
    targets = ("s3:getobject", "s3:getobjectversion")
    for action in actions:
        if not isinstance(action, str):
            continue
        pattern = action.lower()
        if any(fnmatchcase(target, pattern) for target in targets):
            return True
    return False


def _require_nonpublic_bucket_policy(client: Any, *, bucket: str) -> None:
    try:
        response = client.get_bucket_policy(Bucket=bucket)
    except Exception as exc:
        if _provider_error_code(exc) in {
            "404",
            "NoSuchBucketPolicy",
            "NoSuchBucketPolicyException",
            "NotFound",
        }:
            return
        raise ProductionTransportError(
            "production WA-IR bucket policy could not be verified"
        ) from exc
    raw_policy = response.get("Policy") if isinstance(response, dict) else None
    if (
        not isinstance(raw_policy, str)
        or not 1 <= len(raw_policy.encode("utf-8")) <= 64 * 1024
    ):
        raise ProductionTransportError("production WA-IR bucket policy is invalid")
    try:
        policy = json.loads(raw_policy, object_pairs_hook=_strict_json_object)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProductionTransportError(
            "production WA-IR bucket policy is invalid"
        ) from exc
    statements = policy.get("Statement") if isinstance(policy, dict) else None
    statements = [statements] if isinstance(statements, dict) else statements
    if not isinstance(statements, list):
        raise ProductionTransportError("production WA-IR bucket policy is invalid")
    for statement in statements:
        if not isinstance(statement, dict) or statement.get("Effect") != "Allow":
            continue
        public_principal = "NotPrincipal" in statement or _policy_value_has_wildcard(
            statement.get("Principal")
        )
        if public_principal and (
            "NotAction" in statement
            or _policy_action_allows_public_read(statement.get("Action"))
        ):
            raise ProductionTransportError(
                "production WA-IR bucket policy permits public object reads"
            )


def require_private_versioned_bucket(client: Any, *, bucket: str) -> None:
    """Prove that the one approved production bucket is versioned and non-public."""

    if bucket != PRODUCTION_BUCKET:
        raise ProductionTransportError("production WA-IR bucket is not approved")
    try:
        versioning = client.get_bucket_versioning(Bucket=bucket)
        acl = client.get_bucket_acl(Bucket=bucket)
    except Exception as exc:
        raise ProductionTransportError(
            "production WA-IR bucket safety could not be verified"
        ) from exc
    if not isinstance(versioning, dict) or versioning.get("Status") != "Enabled":
        raise ProductionTransportError("production WA-IR bucket is not versioned")
    if (
        not isinstance(acl, dict)
        or not isinstance(acl.get("Grants"), list)
        or not acl["Grants"]
    ):
        raise ProductionTransportError("production WA-IR bucket ACL is invalid")
    for grant in acl["Grants"]:
        grantee = grant.get("Grantee") if isinstance(grant, dict) else None
        if not isinstance(grantee, dict):
            raise ProductionTransportError("production WA-IR bucket ACL is invalid")
        uri = str(grantee.get("URI") or "") if isinstance(grantee, dict) else ""
        if uri.endswith("/AllUsers") or uri.endswith("/AuthenticatedUsers"):
            raise ProductionTransportError("production WA-IR bucket ACL is public")
    _require_nonpublic_bucket_policy(client, bucket=bucket)


def _validate_metadata(values: Mapping[str, str] | None) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for key, value in (values or {}).items():
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or not _METADATA_KEY_RE.fullmatch(key)
            or key in _RESERVED_METADATA
            or not 1 <= len(value) <= 256
            or not value.isascii()
            or any(character in "\r\n" for character in value)
        ):
            raise ProductionTransportError("production WA-IR object metadata is invalid")
        metadata[key] = value
    if len(metadata) > 10:
        raise ProductionTransportError("production WA-IR object metadata is oversized")
    return metadata


def _read_age_recipient(path: Path) -> str:
    try:
        recipient = read_secure_text(
            path,
            label="production WA-IR age recipient",
            max_size=MAX_RECIPIENT_BYTES,
        ).strip()
    except (OSError, SecureFileError) as exc:
        raise ProductionTransportError(
            "production WA-IR age recipient is unavailable or unsafe"
        ) from exc
    if not _AGE_RECIPIENT_RE.fullmatch(recipient):
        raise ProductionTransportError("production WA-IR age recipient is malformed")
    return recipient


def encrypt_age_file(
    source: Path,
    output: Path,
    recipient: str,
    *,
    max_bytes: int,
) -> tuple[str, int]:
    """Encrypt one secure source with age and return the ciphertext identity."""

    if not _AGE_RECIPIENT_RE.fullmatch(recipient):
        raise ProductionTransportError("production WA-IR age recipient is malformed")
    if not AGE_EXECUTABLE.is_file():
        raise ProductionTransportError("age is unavailable at /usr/bin/age")
    try:
        plaintext_sha256, plaintext_bytes = sha256_secure_file(
            source,
            label="production WA-IR plaintext",
            max_size=max_bytes,
        )
    except SecureFileError as exc:
        raise ProductionTransportError(
            "production WA-IR plaintext is unavailable or unsafe"
        ) from exc
    if plaintext_bytes <= 0:
        raise ProductionTransportError("production WA-IR plaintext is empty")

    result = subprocess.run(
        [
            str(AGE_EXECUTABLE),
            "--encrypt",
            "--recipient",
            recipient,
            "--output",
            str(output),
            str(source),
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=3600,
        env=_SAFE_ENV,
    )
    if result.returncode != 0:
        output.unlink(missing_ok=True)
        raise ProductionTransportError("production WA-IR age encryption failed closed")
    try:
        output.chmod(0o600)
        ciphertext_sha256, ciphertext_bytes = sha256_secure_file(
            output,
            label="production WA-IR ciphertext",
            max_size=max_bytes + 1024 * 1024,
        )
        after_sha256, after_bytes = sha256_secure_file(
            source,
            label="production WA-IR plaintext",
            max_size=max_bytes,
        )
    except (OSError, SecureFileError) as exc:
        output.unlink(missing_ok=True)
        raise ProductionTransportError(
            "production WA-IR encrypted artifact is unavailable or unsafe"
        ) from exc
    if (after_sha256, after_bytes) != (plaintext_sha256, plaintext_bytes):
        output.unlink(missing_ok=True)
        raise ProductionTransportError(
            "production WA-IR plaintext changed during encryption"
        )
    if ciphertext_bytes <= 0:
        output.unlink(missing_ok=True)
        raise ProductionTransportError("production WA-IR ciphertext is empty")
    return ciphertext_sha256, ciphertext_bytes


def _unique_object_key(
    *,
    prefix: str,
    operation_id: str,
    artifact_kind: str,
    ciphertext_sha256: str,
    nonce: str,
) -> str:
    prefix = _validate_prefix(prefix)
    operation_id = _validate_operation_id(operation_id)
    if not _ARTIFACT_KIND_RE.fullmatch(artifact_kind):
        raise ProductionTransportError("production WA-IR artifact kind is invalid")
    if not _SHA256_RE.fullmatch(ciphertext_sha256):
        raise ProductionTransportError("production WA-IR ciphertext hash is invalid")
    if not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ProductionTransportError("production WA-IR object nonce is invalid")
    return (
        f"{prefix}/{operation_id}/{artifact_kind}/"
        f"{nonce}-{ciphertext_sha256}.age"
    )


_JOURNAL_FIELDS = frozenset(
    {
        "schema",
        "phase",
        "bucket",
        "prefix",
        "operation_id",
        "artifact_kind",
        "recipient",
        "source_sha256",
        "source_bytes",
        "nonce",
        "requested_metadata",
        "object_key",
        "object_metadata",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "version_id",
    }
)
_JOURNAL_PHASES = frozenset({"initializing", "prepared", "uploaded", "verified"})
_MISSING_OBJECT_CODES = frozenset(
    {"404", "NoSuchKey", "NoSuchObject", "NotFound"}
)


def _require_secure_journal_parent(path: Path) -> None:
    if not path.is_absolute() or not path.name or path.name in {".", ".."}:
        raise ProductionTransportError(
            "production WA-IR publication journal path is invalid"
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path.parent, flags)
    except OSError as exc:
        raise ProductionTransportError(
            "production WA-IR publication journal directory is unavailable"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise ProductionTransportError(
                "production WA-IR publication journal directory is unsafe"
            )
    finally:
        os.close(descriptor)


@contextmanager
def _publication_lock(journal_path: Path):  # noqa: ANN202
    _require_secure_journal_parent(journal_path)
    lock_path = journal_path.with_name(f"{journal_path.name}.lock")
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ProductionTransportError(
            "production WA-IR publication journal lock is unavailable"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ProductionTransportError(
                "production WA-IR publication journal lock is unsafe"
            )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _write_journal(path: Path, state: Mapping[str, Any], *, create: bool) -> None:
    encoded = (
        json.dumps(dict(state), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    try:
        if create:
            write_secure_new_bytes(
                path,
                encoded,
                label="production WA-IR publication journal",
                max_size=MAX_JOURNAL_BYTES,
            )
        else:
            write_secure_atomic_bytes(
                path,
                encoded,
                label="production WA-IR publication journal",
                max_size=MAX_JOURNAL_BYTES,
            )
    except SecureFileError as exc:
        raise ProductionTransportError(
            "production WA-IR publication journal could not be persisted"
        ) from exc


def _load_journal(path: Path) -> dict[str, Any]:
    try:
        payload = read_secure_text(
            path,
            label="production WA-IR publication journal",
            max_size=MAX_JOURNAL_BYTES,
        )
        state = json.loads(payload, object_pairs_hook=_strict_json_object)
    except (OSError, SecureFileError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProductionTransportError(
            "production WA-IR publication journal is unavailable or invalid"
        ) from exc
    if (
        not isinstance(state, dict)
        or set(state) != _JOURNAL_FIELDS
        or state.get("schema") != PUBLICATION_JOURNAL_SCHEMA
        or state.get("phase") not in _JOURNAL_PHASES
    ):
        raise ProductionTransportError(
            "production WA-IR publication journal schema is invalid"
        )
    return state


def _journal_ciphertext_path(journal_path: Path) -> Path:
    return journal_path.with_name(f"{journal_path.name}.payload.age")


def _remove_initializing_ciphertext(path: Path) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ProductionTransportError(
            "production WA-IR initializing ciphertext is unsafe"
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or metadata.st_size > MAX_READBACK_BYTES
    ):
        raise ProductionTransportError(
            "production WA-IR initializing ciphertext is unsafe"
        )
    try:
        path.chmod(0o600, follow_symlinks=False)
        path.unlink()
        directory_fd = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise ProductionTransportError(
            "production WA-IR initializing ciphertext could not be reset"
        ) from exc


def _fsync_secure_file_and_parent(path: Path, *, expected_bytes: int) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_descriptor = directory_descriptor = -1
    try:
        file_descriptor = os.open(path, flags)
        metadata = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != expected_bytes
        ):
            raise ProductionTransportError(
                "production WA-IR journal ciphertext is unsafe"
            )
        directory_descriptor = os.open(path.parent, directory_flags)
        os.fsync(file_descriptor)
        os.fsync(directory_descriptor)
    except OSError as exc:
        raise ProductionTransportError(
            "production WA-IR journal ciphertext could not be made durable"
        ) from exc
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        if file_descriptor >= 0:
            os.close(file_descriptor)


def _validate_journal_request(
    state: Mapping[str, Any],
    *,
    bucket: str,
    prefix: str,
    operation_id: str,
    artifact_kind: str,
    recipient: str,
    source_sha256: str,
    source_bytes: int,
    requested_metadata: Mapping[str, str],
) -> None:
    expected = {
        "bucket": bucket,
        "prefix": prefix,
        "operation_id": operation_id,
        "artifact_kind": artifact_kind,
        "recipient": recipient,
        "source_sha256": source_sha256,
        "source_bytes": source_bytes,
        "requested_metadata": dict(requested_metadata),
    }
    if any(state.get(key) != value for key, value in expected.items()):
        raise ProductionTransportError(
            "production WA-IR publication journal is bound to different inputs"
        )
    nonce = state.get("nonce")
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ProductionTransportError(
            "production WA-IR publication journal nonce is invalid"
        )


def _validate_prepared_journal(
    state: Mapping[str, Any],
    *,
    ciphertext_path: Path,
) -> PublishedObject:
    ciphertext_sha256 = state.get("ciphertext_sha256")
    ciphertext_bytes = state.get("ciphertext_bytes")
    object_key = state.get("object_key")
    object_metadata = state.get("object_metadata")
    version_id = state.get("version_id")
    if (
        not isinstance(ciphertext_sha256, str)
        or not _SHA256_RE.fullmatch(ciphertext_sha256)
        or isinstance(ciphertext_bytes, bool)
        or not isinstance(ciphertext_bytes, int)
        or not 1 <= ciphertext_bytes <= MAX_READBACK_BYTES
        or not isinstance(object_key, str)
        or not isinstance(object_metadata, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in object_metadata.items()
        )
    ):
        raise ProductionTransportError(
            "production WA-IR prepared publication journal is invalid"
        )
    validate_object_key_binding(
        object_key,
        operation_id=str(state["operation_id"]),
        artifact_kind=str(state["artifact_kind"]),
        ciphertext_sha256=ciphertext_sha256,
    )
    expected_metadata = dict(state["requested_metadata"])
    expected_metadata.update(
        {
            "transport-schema": TRANSPORT_SCHEMA,
            "operation-id": str(state["operation_id"]),
            "artifact-kind": str(state["artifact_kind"]),
            "plaintext-sha256": str(state["source_sha256"]),
            "ciphertext-sha256": ciphertext_sha256,
        }
    )
    if object_metadata != expected_metadata:
        raise ProductionTransportError(
            "production WA-IR prepared publication metadata is invalid"
        )
    phase = str(state["phase"])
    if phase in {"uploaded", "verified"}:
        if (
            not isinstance(version_id, str)
            or not 1 <= len(version_id) <= 1024
            or any(character.isspace() for character in version_id)
        ):
            raise ProductionTransportError(
                "production WA-IR publication journal version is invalid"
            )
    elif version_id is not None:
        raise ProductionTransportError(
            "production WA-IR prepared publication has an unexpected version"
        )
    if phase != "verified":
        try:
            observed = sha256_secure_file(
                ciphertext_path,
                label="production WA-IR journal ciphertext",
                max_size=MAX_READBACK_BYTES,
            )
        except SecureFileError as exc:
            raise ProductionTransportError(
                "production WA-IR journal ciphertext is unavailable or unsafe"
            ) from exc
        if observed != (ciphertext_sha256, ciphertext_bytes):
            raise ProductionTransportError(
                "production WA-IR journal ciphertext identity differs"
            )
    return PublishedObject(
        bucket=str(state["bucket"]),
        object_key=object_key,
        version_id=str(version_id or ""),
        plaintext_sha256=str(state["source_sha256"]),
        plaintext_bytes=int(state["source_bytes"]),
        ciphertext_sha256=ciphertext_sha256,
        ciphertext_bytes=ciphertext_bytes,
        metadata=MappingProxyType(dict(object_metadata)),
    )


def _readback_exact_version(
    client: Any,
    *,
    bucket: str,
    object_key: str,
    version_id: str,
    expected_sha256: str,
    expected_bytes: int,
    expected_metadata: Mapping[str, str],
) -> None:
    exact = {"Bucket": bucket, "Key": object_key, "VersionId": version_id}
    try:
        head = client.head_object(**exact)
    except Exception as exc:
        raise ProductionTransportError(
            "production WA-IR exact-version HEAD failed"
        ) from exc
    try:
        head_valid = (
            isinstance(head, dict)
            and str(head.get("VersionId") or "") == version_id
            and int(head.get("ContentLength", -1)) == expected_bytes
            and head.get("Metadata") == dict(expected_metadata)
        )
    except (TypeError, ValueError):
        head_valid = False
    if not head_valid:
        raise ProductionTransportError(
            "production WA-IR exact-version HEAD metadata differs"
        )
    try:
        response = client.get_object(**exact)
    except Exception as exc:
        raise ProductionTransportError(
            "production WA-IR exact-version GET failed"
        ) from exc
    try:
        response_valid = (
            isinstance(response, dict)
            and str(response.get("VersionId") or "") == version_id
            and int(response.get("ContentLength", -1)) == expected_bytes
            and response.get("Metadata") == dict(expected_metadata)
        )
    except (TypeError, ValueError):
        response_valid = False
    if not response_valid:
        raise ProductionTransportError(
            "production WA-IR exact-version GET metadata differs"
        )

    digest = hashlib.sha256()
    observed_bytes = 0
    body = response.get("Body")
    if body is None or not callable(getattr(body, "read", None)):
        raise ProductionTransportError(
            "production WA-IR exact-version GET has no readable body"
        )
    try:
        while True:
            chunk = body.read(1024 * 1024)
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise ProductionTransportError(
                    "production WA-IR exact-version GET returned invalid bytes"
                )
            observed_bytes += len(chunk)
            if observed_bytes > MAX_READBACK_BYTES or observed_bytes > expected_bytes:
                raise ProductionTransportError(
                    "production WA-IR exact-version GET exceeded its bound"
                )
            digest.update(chunk)
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()
    if observed_bytes != expected_bytes or digest.hexdigest() != expected_sha256:
        raise ProductionTransportError(
            "production WA-IR exact-version readback hash or size differs"
        )


def _recover_current_exact_version(
    client: Any,
    published: PublishedObject,
) -> str | None:
    """Recover an accepted PUT without issuing another write."""

    try:
        head = client.head_object(
            Bucket=published.bucket,
            Key=published.object_key,
        )
    except Exception as exc:
        if _provider_error_code(exc) in _MISSING_OBJECT_CODES:
            return None
        raise ProductionTransportError(
            "production WA-IR ambiguous PUT recovery lookup failed"
        ) from exc
    try:
        version_id = str(head.get("VersionId") or "")
        valid = (
            isinstance(head, dict)
            and 1 <= len(version_id) <= 1024
            and not any(character.isspace() for character in version_id)
            and version_id.isprintable()
            and version_id.lower() != "null"
            and int(head.get("ContentLength", -1)) == published.ciphertext_bytes
            and head.get("Metadata") == dict(published.metadata)
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ProductionTransportError(
            "production WA-IR object key is occupied by different content"
        )
    _readback_exact_version(
        client,
        bucket=published.bucket,
        object_key=published.object_key,
        version_id=version_id,
        expected_sha256=published.ciphertext_sha256,
        expected_bytes=published.ciphertext_bytes,
        expected_metadata=published.metadata,
    )
    return version_id


def publish_age_encrypted(
    source: Path,
    *,
    recipient_file: Path,
    bucket: str,
    prefix: str,
    operation_id: str,
    artifact_kind: str,
    client: Any,
    journal_path: Path,
    metadata: Mapping[str, str] | None = None,
    max_bytes: int = MAX_PAYLOAD_BYTES,
) -> PublishedObject:
    """Journal, encrypt, create, and verify one resumable production object."""

    if not 1 <= max_bytes <= MAX_PAYLOAD_BYTES:
        raise ProductionTransportError("production WA-IR payload bound is invalid")
    operation_id = _validate_operation_id(operation_id)
    prefix = _validate_prefix(prefix)
    if not _ARTIFACT_KIND_RE.fullmatch(artifact_kind):
        raise ProductionTransportError("production WA-IR artifact kind is invalid")
    require_private_versioned_bucket(client, bucket=bucket)
    recipient = _read_age_recipient(recipient_file)
    requested_metadata = _validate_metadata(metadata)
    try:
        plaintext_sha256, plaintext_bytes = sha256_secure_file(
            source,
            label="production WA-IR plaintext",
            max_size=max_bytes,
        )
    except SecureFileError as exc:
        raise ProductionTransportError(
            "production WA-IR plaintext is unavailable or unsafe"
        ) from exc
    if plaintext_bytes <= 0:
        raise ProductionTransportError("production WA-IR plaintext is empty")
    ciphertext = _journal_ciphertext_path(journal_path)
    with _publication_lock(journal_path):
        journal_exists = journal_path.exists() or journal_path.is_symlink()
        if journal_exists:
            state = _load_journal(journal_path)
            prepared_in_this_call = False
        else:
            state = {
                "schema": PUBLICATION_JOURNAL_SCHEMA,
                "phase": "initializing",
                "bucket": bucket,
                "prefix": prefix,
                "operation_id": operation_id,
                "artifact_kind": artifact_kind,
                "recipient": recipient,
                "source_sha256": plaintext_sha256,
                "source_bytes": plaintext_bytes,
                "nonce": uuid.uuid4().hex,
                "requested_metadata": requested_metadata,
                "object_key": None,
                "object_metadata": None,
                "ciphertext_sha256": None,
                "ciphertext_bytes": None,
                "version_id": None,
            }
            _write_journal(journal_path, state, create=True)
            prepared_in_this_call = False

        _validate_journal_request(
            state,
            bucket=bucket,
            prefix=prefix,
            operation_id=operation_id,
            artifact_kind=artifact_kind,
            recipient=recipient,
            source_sha256=plaintext_sha256,
            source_bytes=plaintext_bytes,
            requested_metadata=requested_metadata,
        )
        if state["phase"] == "initializing":
            if any(
                state.get(key) is not None
                for key in (
                    "object_key",
                    "object_metadata",
                    "ciphertext_sha256",
                    "ciphertext_bytes",
                    "version_id",
                )
            ):
                raise ProductionTransportError(
                    "production WA-IR initializing journal is invalid"
                )
            _remove_initializing_ciphertext(ciphertext)
            ciphertext_sha256, ciphertext_bytes = encrypt_age_file(
                source,
                ciphertext,
                recipient,
                max_bytes=max_bytes,
            )
            try:
                observed_ciphertext = sha256_secure_file(
                    ciphertext,
                    label="production WA-IR journal ciphertext",
                    max_size=MAX_READBACK_BYTES,
                )
                observed_plaintext = sha256_secure_file(
                    source,
                    label="production WA-IR plaintext",
                    max_size=max_bytes,
                )
            except SecureFileError as exc:
                raise ProductionTransportError(
                    "production WA-IR encrypted artifact is unavailable or unsafe"
                ) from exc
            if observed_ciphertext != (ciphertext_sha256, ciphertext_bytes):
                raise ProductionTransportError(
                    "production WA-IR encryptor returned an invalid artifact identity"
                )
            if observed_plaintext != (plaintext_sha256, plaintext_bytes):
                raise ProductionTransportError(
                    "production WA-IR plaintext changed during encryption"
                )
            _fsync_secure_file_and_parent(
                ciphertext,
                expected_bytes=ciphertext_bytes,
            )
            object_metadata = dict(requested_metadata)
            object_metadata.update(
                {
                    "transport-schema": TRANSPORT_SCHEMA,
                    "operation-id": operation_id,
                    "artifact-kind": artifact_kind,
                    "plaintext-sha256": plaintext_sha256,
                    "ciphertext-sha256": ciphertext_sha256,
                }
            )
            state.update(
                {
                    "phase": "prepared",
                    "object_key": _unique_object_key(
                        prefix=prefix,
                        operation_id=operation_id,
                        artifact_kind=artifact_kind,
                        ciphertext_sha256=ciphertext_sha256,
                        nonce=str(state["nonce"]),
                    ),
                    "object_metadata": object_metadata,
                    "ciphertext_sha256": ciphertext_sha256,
                    "ciphertext_bytes": ciphertext_bytes,
                }
            )
            _write_journal(journal_path, state, create=False)
            prepared_in_this_call = True

        published = _validate_prepared_journal(
            state,
            ciphertext_path=ciphertext,
        )
        if state["phase"] == "verified":
            _validate_published_object(published)
            return published

        if state["phase"] == "prepared":
            recovered_version: str | None = None
            if not prepared_in_this_call:
                recovered_version = _recover_current_exact_version(client, published)
            if recovered_version is None:
                put_error: Exception | None = None
                response: Any = None
                try:
                    with ciphertext.open("rb") as body:
                        response = client.put_object(
                            Bucket=bucket,
                            Key=published.object_key,
                            Body=body,
                            ContentLength=published.ciphertext_bytes,
                            ContentType="application/octet-stream",
                            Metadata=dict(published.metadata),
                            ACL="private",
                            IfNoneMatch="*",
                        )
                except Exception as exc:
                    put_error = exc
                raw_version_id = (
                    response.get("VersionId")
                    if isinstance(response, dict)
                    else None
                )
                candidate_version = str(raw_version_id or "")
                if (
                    put_error is None
                    and candidate_version
                    and candidate_version.lower() != "null"
                    and candidate_version == candidate_version.strip()
                    and candidate_version.isprintable()
                    and len(candidate_version) <= 1024
                ):
                    recovered_version = candidate_version
                else:
                    try:
                        recovered_version = _recover_current_exact_version(
                            client,
                            published,
                        )
                    except ProductionTransportError as recovery_error:
                        raise ProductionTransportError(
                            "production WA-IR PUT outcome is ambiguous; "
                            "retry only with the same publication journal"
                        ) from recovery_error
                    if recovered_version is None:
                        raise ProductionTransportError(
                            "production WA-IR PUT was not proven; "
                            "retry only with the same publication journal"
                        ) from put_error
            state["phase"] = "uploaded"
            state["version_id"] = recovered_version
            _write_journal(journal_path, state, create=False)
            published = _validate_prepared_journal(
                state,
                ciphertext_path=ciphertext,
            )

        _readback_exact_version(
            client,
            bucket=published.bucket,
            object_key=published.object_key,
            version_id=published.version_id,
            expected_sha256=published.ciphertext_sha256,
            expected_bytes=published.ciphertext_bytes,
            expected_metadata=published.metadata,
        )
        state["phase"] = "verified"
        _write_journal(journal_path, state, create=False)
        published = _validate_prepared_journal(
            state,
            ciphertext_path=ciphertext,
        )
        _validate_published_object(published)
        return published


def _validate_published_object(published: PublishedObject) -> None:
    try:
        operation_id = str(published.metadata.get("operation-id") or "")
        artifact_kind = str(published.metadata.get("artifact-kind") or "")
        validate_object_key_binding(
            published.object_key,
            operation_id=operation_id,
            artifact_kind=artifact_kind,
            ciphertext_sha256=published.ciphertext_sha256,
        )
        if (
            published.bucket != PRODUCTION_BUCKET
            or not 1 <= len(published.version_id) <= 1024
            or any(character.isspace() for character in published.version_id)
            or not _SHA256_RE.fullmatch(published.plaintext_sha256)
            or not _SHA256_RE.fullmatch(published.ciphertext_sha256)
            or published.plaintext_bytes <= 0
            or published.ciphertext_bytes <= 0
            or published.metadata.get("transport-schema") != TRANSPORT_SCHEMA
            or published.metadata.get("operation-id") != operation_id
            or published.metadata.get("artifact-kind") != artifact_kind
            or published.metadata.get("plaintext-sha256")
            != published.plaintext_sha256
            or published.metadata.get("ciphertext-sha256")
            != published.ciphertext_sha256
        ):
            raise ValueError
    except (AttributeError, TypeError, ValueError, ProductionTransportError) as exc:
        raise ProductionTransportError(
            "production WA-IR published object is invalid"
        ) from exc


def presign_exact_get(
    client: Any,
    published: PublishedObject,
    *,
    ttl_seconds: int,
) -> EphemeralPresignedGet:
    """Mint a redacted wrapper around one short-lived exact-version GET URL."""

    if not MIN_URL_TTL_SECONDS <= ttl_seconds <= MAX_URL_TTL_SECONDS:
        raise ProductionTransportError(
            "production WA-IR presigned URL lifetime must be between 60 and 900 seconds"
        )
    _validate_published_object(published)
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": published.bucket,
                "Key": published.object_key,
                "VersionId": published.version_id,
            },
            ExpiresIn=ttl_seconds,
        )
    except Exception as exc:
        raise ProductionTransportError(
            "production WA-IR exact-version URL generation failed"
        ) from exc
    try:
        parsed = urlsplit(str(url))
        query = parse_qs(parsed.query, keep_blank_values=True)
        versions = query.get("versionId", [])
        expirations = query.get("X-Amz-Expires", [])
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ProductionTransportError(
            "production WA-IR presigned URL is malformed"
        ) from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != ARVAN_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or unquote(parsed.path)
        != f"/{published.bucket}/{published.object_key}"
        or versions != [published.version_id]
        or expirations != [str(ttl_seconds)]
    ):
        raise ProductionTransportError(
            "production WA-IR presigned URL is not one exact Arvan object version"
        )
    return EphemeralPresignedGet(
        str(url),
        expires_in_seconds=ttl_seconds,
        object_key=published.object_key,
        version_id=published.version_id,
    )
