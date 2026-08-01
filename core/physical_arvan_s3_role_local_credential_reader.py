"""Secure fixed-file reader used by one-role Arvan S3 artifacts only.

This neutral primitive validates one fixed machine-user file under a
role-specific artifact's fixed policy.  It has no paired credential API, no
second credential path, no SDK/client/provider import, and no network action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from core.physical_arvan_s3_role_local_route_policy import (
    ArvanS3RoleLocalRoutePolicy,
    validate_physical_arvan_s3_role_local_route_policy,
)


__all__ = (
    "PHYSICAL_ARVAN_S3_MACHINE_USER_CREDENTIAL_SCHEMA",
    "ArvanS3RoleLocalCredentialFacts",
    "ArvanS3RoleLocalCredentialReaderError",
    "ArvanS3RoleLocalRouteFacts",
    "load_root_owned_arvan_s3_role_local_credential",
)


PHYSICAL_ARVAN_S3_MACHINE_USER_CREDENTIAL_SCHEMA = (
    "gold-trade-physical-arvan-s3-machine-user-credential-v1"
)
_MAX_BYTES = 16 * 1024
_MAX_VALUE_BYTES = 1024
_VALUE_RE = re.compile(r"^[\x21-\x7e]{1,1024}$", re.ASCII)
_IDENTITY_DOMAIN = b"gold-trade-arvan-s3-machine-user-identity-v1\x00"


class ArvanS3RoleLocalCredentialReaderError(ValueError):
    """Fixed redacted failure from a single-role secure reader."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ArvanS3RoleLocalRouteFacts:
    """Normalized non-secret route facts local to one credential reader."""

    endpoint: str
    region: str
    bucket: str


@dataclass(frozen=True)
class ArvanS3RoleLocalCredentialFacts:
    """Short-lived private key facts; never serialize or log this value."""

    access_key: str = field(repr=False, compare=False)
    secret_key: str = field(repr=False, compare=False)
    identity_sha256: str
    device: int = field(repr=False, compare=False)
    inode: int = field(repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_SERIALIZATION_FORBIDDEN")


def _fail(code: str) -> None:
    raise ArvanS3RoleLocalCredentialReaderError(code)


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_ROOT_REQUIRED")
    except OSError:
        _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_ROOT_REQUIRED")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_FILE_INVALID")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    del value
    _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_FILE_INVALID")


def _credential_value(value: object) -> str:
    if (
        type(value) is not str
        or _VALUE_RE.fullmatch(value) is None
        or len(value.encode("ascii", "strict")) > _MAX_VALUE_BYTES
    ):
        _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_FILE_INVALID")
    return value


def _identity_sha256(access_key: str) -> str:
    return hashlib.sha256(_IDENTITY_DOMAIN + access_key.encode("ascii")).hexdigest()


def _fixed_private_path(path: object) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_FILE_INVALID")
    parent = path.parent
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
        parent_metadata = os.lstat(parent)
        parent_resolved = parent.resolve(strict=True)
    except OSError:
        _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_FILE_INVALID")
    if (
        resolved != path
        or parent_resolved != parent
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or metadata.st_uid != 0
        or parent_metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
        or metadata.st_size < 2
        or metadata.st_size > _MAX_BYTES
        or not hasattr(os, "O_NOFOLLOW")
    ):
        _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_FILE_INVALID")
    return resolved


def _read_private_file(path: Path) -> tuple[bytes, int, int]:
    descriptor = -1
    try:
        before = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size < 2
            or metadata.st_size > _MAX_BYTES
            or before.st_dev != metadata.st_dev
            or before.st_ino != metadata.st_ino
        ):
            _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_FILE_INVALID")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 4096)
            if type(chunk) is not bytes:
                _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_FILE_INVALID")
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_BYTES:
                _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_FILE_INVALID")
            chunks.append(chunk)
        after = os.lstat(path)
        if (
            total != metadata.st_size
            or after.st_dev != metadata.st_dev
            or after.st_ino != metadata.st_ino
            or after.st_uid != 0
            or after.st_nlink != 1
            or stat.S_ISLNK(after.st_mode)
            or stat.S_IMODE(after.st_mode) != 0o600
        ):
            _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_FILE_INVALID")
        return b"".join(chunks), metadata.st_dev, metadata.st_ino
    except ArvanS3RoleLocalCredentialReaderError:
        raise
    except OSError:
        _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_FILE_INVALID")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_FILE_INVALID")


def _load_credential(
    path: Path,
    *,
    expected_role: str,
    expected_action_profile: str,
) -> ArvanS3RoleLocalCredentialFacts:
    raw, device, inode = _read_private_file(_fixed_private_path(path))
    try:
        parsed = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except ArvanS3RoleLocalCredentialReaderError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_FILE_INVALID")
    if type(parsed) is not dict or set(parsed) != {
        "schema",
        "role",
        "action_profile",
        "access_key",
        "secret_key",
    }:
        _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_FILE_INVALID")
    if (
        parsed["schema"] != PHYSICAL_ARVAN_S3_MACHINE_USER_CREDENTIAL_SCHEMA
        or parsed["role"] != expected_role
        or parsed["action_profile"] != expected_action_profile
    ):
        _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_SCOPE_INVALID")
    access_key = _credential_value(parsed["access_key"])
    secret_key = _credential_value(parsed["secret_key"])
    return ArvanS3RoleLocalCredentialFacts(
        access_key=access_key,
        secret_key=secret_key,
        identity_sha256=_identity_sha256(access_key),
        device=device,
        inode=inode,
    )


def load_root_owned_arvan_s3_role_local_credential(
    *,
    route_policy: ArvanS3RoleLocalRoutePolicy,
    expected_source_site: str,
    expected_destination_site: str,
    expected_object_storage_namespace: str,
    expected_role: str,
    expected_action_profile: str,
    fixed_credential_file: Path,
) -> tuple[ArvanS3RoleLocalRouteFacts, ArvanS3RoleLocalCredentialFacts]:
    """Validate one fixed route and open exactly one fixed credential file."""

    try:
        policy = validate_physical_arvan_s3_role_local_route_policy(
            route_policy,
            expected_source_site=expected_source_site,
            expected_destination_site=expected_destination_site,
            expected_object_storage_namespace=expected_object_storage_namespace,
            require_enabled=True,
        )
    except Exception:
        _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_ROUTE_POLICY_INVALID")
    if (
        type(expected_role) is not str
        or type(expected_action_profile) is not str
        or not expected_role
        or not expected_action_profile
    ):
        _fail("ARVAN_S3_ROLE_LOCAL_CREDENTIAL_SCOPE_INVALID")
    _require_root()
    credential = _load_credential(
        fixed_credential_file,
        expected_role=expected_role,
        expected_action_profile=expected_action_profile,
    )
    return (
        ArvanS3RoleLocalRouteFacts(
            endpoint=policy.endpoint,
            region=policy.region,
            bucket=policy.bucket,
        ),
        credential,
    )
