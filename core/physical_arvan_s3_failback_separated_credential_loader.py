"""Root-only admission for the reverse Arvan Object-Storage identities.

This is the credential boundary for the *other* half of the four identity
model: the promoted WA-IR ``ir-publisher`` and rebuilding WA-FI
``fi-receiver`` machine users.  It is intentionally a separate module from
the normal FI-publisher/IR-receiver loader.  In particular, neither normal
credential path is imported, accepted, or opened here.

The module is inert until an explicit root-only call.  It validates two fixed
root-owned files, releases only one-way public identity fingerprints, and
discards secret material.  It does not construct an SDK client, contact
Object Storage, open a socket, or perform any direct site control.
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

from core.physical_arvan_s3_client_factory import (
    ARVAN_S3_CLIENT_FACTORY_SCHEMA,
    RootOwnedArvanS3ClientFactoryConfig,
    validate_root_owned_arvan_s3_client_factory_config,
)
from core import physical_arvan_s3_role_profiles as _role_profiles
from core.physical_wal_object_manifest import PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE


__all__ = (
    "ARVAN_S3_FI_RECEIVER_EXPECTED_PROBE_ACTIONS",
    "ARVAN_S3_IR_PUBLISHER_EXPECTED_PROBE_ACTIONS",
    "ARVAN_S3_FAILBACK_SEPARATED_LEGACY_PAIRED_API_STATUS",
    "FIXED_ARVAN_S3_FI_RECEIVER_CREDENTIAL_FILE",
    "FIXED_ARVAN_S3_IR_PUBLISHER_CREDENTIAL_FILE",
    "PHYSICAL_ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_LOADER_DEFAULT_ENABLED",
    "PHYSICAL_ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_LOADER_SCHEMA",
    "PHYSICAL_ARVAN_S3_MACHINE_USER_CREDENTIAL_SCHEMA",
    "ArvanS3FailbackSeparatedCredentialLoaderError",
    "ArvanS3FailbackSeparatedCredentialProjection",
    "RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig",
    "VerifiedArvanS3FailbackSeparatedCredentialPair",
    "load_root_owned_arvan_s3_failback_separated_credential_pair",
    "project_root_owned_arvan_s3_failback_separated_credentials",
    "require_verified_arvan_s3_failback_separated_credential_pair",
    "validate_root_owned_arvan_s3_failback_separated_credential_loader_config",
)


PHYSICAL_ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_LOADER_SCHEMA = (
    "gold-trade-physical-arvan-s3-failback-separated-credential-loader-v1"
)
PHYSICAL_ARVAN_S3_MACHINE_USER_CREDENTIAL_SCHEMA = (
    "gold-trade-physical-arvan-s3-machine-user-credential-v1"
)
PHYSICAL_ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_LOADER_DEFAULT_ENABLED = False
ARVAN_S3_FAILBACK_SEPARATED_LEGACY_PAIRED_API_STATUS = "tombstoned-no-production-route-v1"

# These are deliberately not configurable.  They live on different hosts in
# the deployed topology; local unit tests may patch the constants only inside
# a process.  No normal-direction path appears in this module.
FIXED_ARVAN_S3_IR_PUBLISHER_CREDENTIAL_FILE = Path(
    "/etc/trading-bot/security/arvan-s3-ir-publisher-credentials.json"
)
FIXED_ARVAN_S3_FI_RECEIVER_CREDENTIAL_FILE = Path(
    "/etc/trading-bot/security/arvan-s3-fi-receiver-credentials.json"
)

ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_MAX_BYTES = 16 * 1024
ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_MAX_VALUE_BYTES = 1024

_SOURCE_SITE = "webapp_ir"
_DESTINATION_SITE = "webapp_fi"
_IR_ROLE = _role_profiles.ARVAN_S3_IR_PUBLISHER_ROLE
_FI_ROLE = _role_profiles.ARVAN_S3_FI_RECEIVER_ROLE
_IR_ACTION_PROFILE = _role_profiles.ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE
_FI_ACTION_PROFILE = _role_profiles.ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE
_IDENTITY_FINGERPRINT_DOMAIN = b"gold-trade-arvan-s3-machine-user-identity-v1\x00"
_CREDENTIAL_VALUE_RE = re.compile(r"^[\x21-\x7e]{1,1024}$", re.ASCII)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_PAIR_CAPABILITY = object()

# This is a local expected surface for a later provider IAM preflight, not a
# claim that a JSON file itself proves provider-side policy.
ARVAN_S3_IR_PUBLISHER_EXPECTED_PROBE_ACTIONS = (
    _role_profiles.ARVAN_S3_IR_PUBLISHER_EXPECTED_ACTIONS
)
ARVAN_S3_FI_RECEIVER_EXPECTED_PROBE_ACTIONS = (
    _role_profiles.ARVAN_S3_FI_RECEIVER_EXPECTED_ACTIONS
)


class ArvanS3FailbackSeparatedCredentialLoaderError(ValueError):
    """Fixed redacted refusal; it never includes a path or secret."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig:
    """Default-off, non-secret fixed policy for only the reverse two roles."""

    schema: str = PHYSICAL_ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_LOADER_SCHEMA
    endpoint: str = ""
    region: str = ""
    bucket: str = ""
    enabled: bool = PHYSICAL_ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_LOADER_DEFAULT_ENABLED
    source_site: str = _SOURCE_SITE
    destination_site: str = _DESTINATION_SITE
    object_storage_namespace: str = PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
    ir_publisher_action_profile: str = _IR_ACTION_PROFILE
    fi_receiver_action_profile: str = _FI_ACTION_PROFILE
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class VerifiedArvanS3FailbackSeparatedCredentialPair:
    """Opaque public reverse-role admission; it contains no key material."""

    schema: str
    endpoint: str
    region: str
    bucket: str
    source_site: str
    destination_site: str
    object_storage_namespace: str
    ir_publisher_identity_sha256: str
    fi_receiver_identity_sha256: str
    ir_publisher_action_profile: str
    fi_receiver_action_profile: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class ArvanS3FailbackSeparatedCredentialProjection:
    """Public identities and bounded expected actions for reverse preflight."""

    schema: str
    ir_publisher_role: str
    ir_publisher_identity_sha256: str
    ir_publisher_action_profile: str
    ir_publisher_allowed_operations: tuple[str, ...]
    fi_receiver_role: str
    fi_receiver_identity_sha256: str
    fi_receiver_action_profile: str
    fi_receiver_allowed_operations: tuple[str, ...]


@dataclass(frozen=True)
class _ConfigFacts:
    endpoint: str
    region: str
    bucket: str


@dataclass(frozen=True)
class _CredentialFacts:
    access_key: str = field(repr=False, compare=False)
    secret_key: str = field(repr=False, compare=False)
    identity_sha256: str
    device: int = field(repr=False, compare=False)
    inode: int = field(repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_FAILBACK_PRIVATE_CREDENTIAL_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _LoadedCredentialFacts:
    config: _ConfigFacts
    ir_publisher: _CredentialFacts = field(repr=False, compare=False)
    fi_receiver: _CredentialFacts = field(repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_FAILBACK_PRIVATE_CREDENTIAL_SERIALIZATION_FORBIDDEN")


def _fail(code: str) -> None:
    raise ArvanS3FailbackSeparatedCredentialLoaderError(code)


def _require_root() -> None:
    if os.geteuid() != 0:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_ROOT_REQUIRED")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_INVALID")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    del value
    _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_INVALID")


def _credential_value(value: object) -> str:
    if (
        type(value) is not str
        or _CREDENTIAL_VALUE_RE.fullmatch(value) is None
        or len(value.encode("ascii", "strict")) > ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_MAX_VALUE_BYTES
    ):
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_INVALID")
    return value


def _identity_sha256(access_key: str) -> str:
    return hashlib.sha256(_IDENTITY_FINGERPRINT_DOMAIN + access_key.encode("ascii")).hexdigest()


def _fixed_private_path(path: object) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_INVALID")
    parent = path.parent
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
        parent_metadata = os.lstat(parent)
        parent_resolved = parent.resolve(strict=True)
    except OSError:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_INVALID")
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
        or metadata.st_size > ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_MAX_BYTES
        or not hasattr(os, "O_NOFOLLOW")
    ):
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_INVALID")
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
            or metadata.st_size > ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_MAX_BYTES
            or before.st_dev != metadata.st_dev
            or before.st_ino != metadata.st_ino
        ):
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_INVALID")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 4096)
            if type(chunk) is not bytes:
                _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_INVALID")
            if not chunk:
                break
            total += len(chunk)
            if total > ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_MAX_BYTES:
                _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_INVALID")
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
            _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_INVALID")
        return b"".join(chunks), metadata.st_dev, metadata.st_ino
    except ArvanS3FailbackSeparatedCredentialLoaderError:
        raise
    except OSError:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_INVALID")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_INVALID")


def _load_credential(
    path: Path,
    *,
    expected_role: str,
    expected_action_profile: str,
) -> _CredentialFacts:
    raw, device, inode = _read_private_file(_fixed_private_path(path))
    try:
        decoded = raw.decode("utf-8", "strict")
        value = json.loads(
            decoded,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except ArvanS3FailbackSeparatedCredentialLoaderError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_INVALID")
    if type(value) is not dict or set(value) != {
        "schema",
        "role",
        "action_profile",
        "access_key",
        "secret_key",
    }:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_INVALID")
    if (
        value["schema"] != PHYSICAL_ARVAN_S3_MACHINE_USER_CREDENTIAL_SCHEMA
        or value["role"] != expected_role
        or value["action_profile"] != expected_action_profile
    ):
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_SCOPE_INVALID")
    access_key = _credential_value(value["access_key"])
    secret_key = _credential_value(value["secret_key"])
    return _CredentialFacts(
        access_key=access_key,
        secret_key=secret_key,
        identity_sha256=_identity_sha256(access_key),
        device=device,
        inode=inode,
    )


def _config_facts(
    config: object,
    *,
    require_enabled: bool,
) -> _ConfigFacts:
    if type(config) is not RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_CONFIG_INVALID")
    if config.schema != PHYSICAL_ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_LOADER_SCHEMA:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_CONFIG_INVALID")
    if type(config.enabled) is not bool:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_CONFIG_INVALID")
    if require_enabled and config.enabled is not True:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_LOADER_DISABLED")
    try:
        _role_profiles.require_canonical_arvan_s3_role_profile(
            role=_IR_ROLE,
            action_profile=config.ir_publisher_action_profile,
        )
        _role_profiles.require_canonical_arvan_s3_role_profile(
            role=_FI_ROLE,
            action_profile=config.fi_receiver_action_profile,
        )
    except _role_profiles.ArvanS3RoleProfileError:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_ROUTE_OR_ACTION_INVALID")
    if (
        config.source_site != _SOURCE_SITE
        or config.destination_site != _DESTINATION_SITE
        or config.object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
        or config.ir_publisher_action_profile != _IR_ACTION_PROFILE
        or config.fi_receiver_action_profile != _FI_ACTION_PROFILE
        or config.direct_site_control != "forbidden"
        or config.destination_object_ingest != "pull-only"
    ):
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_ROUTE_OR_ACTION_INVALID")
    try:
        validated = validate_root_owned_arvan_s3_client_factory_config(
            RootOwnedArvanS3ClientFactoryConfig(
                schema=ARVAN_S3_CLIENT_FACTORY_SCHEMA,
                endpoint=config.endpoint,
                region=config.region,
                bucket=config.bucket,
                enabled=config.enabled,
                direct_site_control="forbidden",
                destination_object_ingest="pull-only",
            )
        )
    except Exception:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_CONFIG_INVALID")
    return _ConfigFacts(endpoint=validated.endpoint, region=validated.region, bucket=validated.bucket)


def validate_root_owned_arvan_s3_failback_separated_credential_loader_config(
    config: RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig,
) -> RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig:
    """Pure validation; this does not read either reverse credential file."""

    facts = _config_facts(config, require_enabled=False)
    return RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig(
        schema=PHYSICAL_ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_LOADER_SCHEMA,
        endpoint=facts.endpoint,
        region=facts.region,
        bucket=facts.bucket,
        enabled=config.enabled,
        source_site=_SOURCE_SITE,
        destination_site=_DESTINATION_SITE,
        object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        ir_publisher_action_profile=_IR_ACTION_PROFILE,
        fi_receiver_action_profile=_FI_ACTION_PROFILE,
        direct_site_control="forbidden",
        destination_object_ingest="pull-only",
    )


def _load_root_owned_failback_separated_credential_facts(
    config: RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig,
) -> _LoadedCredentialFacts:
    facts = _config_facts(config, require_enabled=True)
    _require_root()
    ir_path = FIXED_ARVAN_S3_IR_PUBLISHER_CREDENTIAL_FILE
    fi_path = FIXED_ARVAN_S3_FI_RECEIVER_CREDENTIAL_FILE
    if ir_path == fi_path:
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_FILE_COLLISION")
    ir = _load_credential(ir_path, expected_role=_IR_ROLE, expected_action_profile=_IR_ACTION_PROFILE)
    fi = _load_credential(fi_path, expected_role=_FI_ROLE, expected_action_profile=_FI_ACTION_PROFILE)
    if (
        (ir.device, ir.inode) == (fi.device, fi.inode)
        or ir.identity_sha256 == fi.identity_sha256
        or ir.access_key == fi.access_key
        or ir.secret_key == fi.secret_key
    ):
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIALS_NOT_SEPARATE")
    return _LoadedCredentialFacts(config=facts, ir_publisher=ir, fi_receiver=fi)


def _load_root_owned_ir_publisher_credential_facts(
    config: RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig,
) -> tuple[_ConfigFacts, _CredentialFacts]:
    """Open only the promoted WA-IR publisher secret for one local callback."""

    facts = _config_facts(config, require_enabled=True)
    _require_root()
    return facts, _load_credential(
        FIXED_ARVAN_S3_IR_PUBLISHER_CREDENTIAL_FILE,
        expected_role=_IR_ROLE,
        expected_action_profile=_IR_ACTION_PROFILE,
    )


def _load_root_owned_fi_receiver_credential_facts(
    config: RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig,
) -> tuple[_ConfigFacts, _CredentialFacts]:
    """Open only the rebuilding WA-FI exact-reader secret for one callback."""

    facts = _config_facts(config, require_enabled=True)
    _require_root()
    return facts, _load_credential(
        FIXED_ARVAN_S3_FI_RECEIVER_CREDENTIAL_FILE,
        expected_role=_FI_ROLE,
        expected_action_profile=_FI_ACTION_PROFILE,
    )


def load_root_owned_arvan_s3_failback_separated_credential_pair(
    config: RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig,
) -> VerifiedArvanS3FailbackSeparatedCredentialPair:
    """Legacy compatibility-only reverse pair; never a production route."""

    loaded = _load_root_owned_failback_separated_credential_facts(config)
    result = VerifiedArvanS3FailbackSeparatedCredentialPair(
        schema=PHYSICAL_ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_LOADER_SCHEMA,
        endpoint=loaded.config.endpoint,
        region=loaded.config.region,
        bucket=loaded.config.bucket,
        source_site=_SOURCE_SITE,
        destination_site=_DESTINATION_SITE,
        object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        ir_publisher_identity_sha256=loaded.ir_publisher.identity_sha256,
        fi_receiver_identity_sha256=loaded.fi_receiver.identity_sha256,
        ir_publisher_action_profile=_IR_ACTION_PROFILE,
        fi_receiver_action_profile=_FI_ACTION_PROFILE,
    )
    object.__setattr__(result, "_capability", _PAIR_CAPABILITY)
    return result


def _pair_facts(
    value: object,
    *,
    config: RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig,
) -> tuple[VerifiedArvanS3FailbackSeparatedCredentialPair, _ConfigFacts]:
    facts = _config_facts(config, require_enabled=True)
    _require_root()
    if (
        type(value) is not VerifiedArvanS3FailbackSeparatedCredentialPair
        or value._capability is not _PAIR_CAPABILITY
    ):
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_PAIR_REQUIRED")
    if (
        value.schema != PHYSICAL_ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_LOADER_SCHEMA
        or value.endpoint != facts.endpoint
        or value.region != facts.region
        or value.bucket != facts.bucket
        or value.source_site != _SOURCE_SITE
        or value.destination_site != _DESTINATION_SITE
        or value.object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
        or value.ir_publisher_action_profile != _IR_ACTION_PROFILE
        or value.fi_receiver_action_profile != _FI_ACTION_PROFILE
        or type(value.ir_publisher_identity_sha256) is not str
        or type(value.fi_receiver_identity_sha256) is not str
        or _SHA256_RE.fullmatch(value.ir_publisher_identity_sha256) is None
        or _SHA256_RE.fullmatch(value.fi_receiver_identity_sha256) is None
        or value.ir_publisher_identity_sha256 == "0" * 64
        or value.fi_receiver_identity_sha256 == "0" * 64
        or value.ir_publisher_identity_sha256 == value.fi_receiver_identity_sha256
    ):
        _fail("ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_PAIR_TAMPERED")
    return value, facts


def require_verified_arvan_s3_failback_separated_credential_pair(
    value: object,
    *,
    config: RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig,
) -> VerifiedArvanS3FailbackSeparatedCredentialPair:
    """Recheck one opaque public reverse admission without reopening files."""

    pair, _facts = _pair_facts(value, config=config)
    return pair


def project_root_owned_arvan_s3_failback_separated_credentials(
    pair: VerifiedArvanS3FailbackSeparatedCredentialPair,
    *,
    config: RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig,
) -> ArvanS3FailbackSeparatedCredentialProjection:
    """Project only facts suitable for a redacted four-role preflight."""

    verified, _facts = _pair_facts(pair, config=config)
    return ArvanS3FailbackSeparatedCredentialProjection(
        schema=PHYSICAL_ARVAN_S3_FAILBACK_SEPARATED_CREDENTIAL_LOADER_SCHEMA,
        ir_publisher_role=_IR_ROLE,
        ir_publisher_identity_sha256=verified.ir_publisher_identity_sha256,
        ir_publisher_action_profile=_IR_ACTION_PROFILE,
        ir_publisher_allowed_operations=ARVAN_S3_IR_PUBLISHER_EXPECTED_PROBE_ACTIONS,
        fi_receiver_role=_FI_ROLE,
        fi_receiver_identity_sha256=verified.fi_receiver_identity_sha256,
        fi_receiver_action_profile=_FI_ACTION_PROFILE,
        fi_receiver_allowed_operations=ARVAN_S3_FI_RECEIVER_EXPECTED_PROBE_ACTIONS,
    )
