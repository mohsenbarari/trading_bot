"""Root-only admission for two separate Arvan Object-Storage machine users.

This is deliberately a credential *admission* boundary, not an S3 client
factory.  It can read only two fixed root-owned files, validates their role
and action profile, computes public one-way identity fingerprints, and then
discards the key material.  It never imports an SDK, constructs a client,
contacts a provider, starts a probe, opens a socket, or executes a command.

The public result contains no access key, secret key, file path, URL, bucket
credential, or client.  Its projection is shaped for the existing injected
immutability probe: deployment code must still construct two independently
scoped clients in a separately reviewed credential-to-client boundary.
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


__all__ = (
    "ARVAN_S3_FI_PUBLISHER_EXPECTED_PROBE_ACTIONS",
    "ARVAN_S3_IR_RECEIVER_EXPECTED_PROBE_ACTIONS",
    "ARVAN_S3_SEPARATED_LEGACY_PAIRED_API_STATUS",
    "FIXED_ARVAN_S3_FI_PUBLISHER_CREDENTIAL_FILE",
    "FIXED_ARVAN_S3_IR_RECEIVER_CREDENTIAL_FILE",
    "PHYSICAL_ARVAN_S3_MACHINE_USER_CREDENTIAL_SCHEMA",
    "PHYSICAL_ARVAN_S3_SEPARATED_CREDENTIAL_LOADER_DEFAULT_ENABLED",
    "PHYSICAL_ARVAN_S3_SEPARATED_CREDENTIAL_LOADER_SCHEMA",
    "PHYSICAL_ARVAN_S3_SEPARATED_CREDENTIAL_PROJECTION_SCHEMA",
    "ArvanS3SeparatedCredentialLoaderError",
    "ArvanS3ImmutabilityProbeCredentialProjection",
    "RootOwnedArvanS3SeparatedCredentialLoaderConfig",
    "VerifiedArvanS3SeparatedCredentialPair",
    "load_root_owned_arvan_s3_separated_credential_pair",
    "project_root_owned_arvan_s3_immutability_probe_credentials",
    "require_verified_arvan_s3_separated_credential_pair",
    "validate_root_owned_arvan_s3_separated_credential_loader_config",
)


PHYSICAL_ARVAN_S3_SEPARATED_CREDENTIAL_LOADER_SCHEMA = (
    "gold-trade-physical-arvan-s3-separated-credential-loader-v1"
)
PHYSICAL_ARVAN_S3_MACHINE_USER_CREDENTIAL_SCHEMA = (
    "gold-trade-physical-arvan-s3-machine-user-credential-v1"
)
PHYSICAL_ARVAN_S3_SEPARATED_CREDENTIAL_PROJECTION_SCHEMA = (
    "gold-trade-physical-arvan-s3-immutability-probe-credential-projection-v1"
)
PHYSICAL_ARVAN_S3_SEPARATED_CREDENTIAL_LOADER_DEFAULT_ENABLED = False
ARVAN_S3_SEPARATED_LEGACY_PAIRED_API_STATUS = "tombstoned-no-production-route-v1"

# Paths are constants rather than configuration.  A deployment may provision
# these exact files but cannot redirect one role to the other role's file or
# to an arbitrary credential path.
FIXED_ARVAN_S3_FI_PUBLISHER_CREDENTIAL_FILE = Path(
    "/etc/trading-bot/security/arvan-s3-fi-publisher-credentials.json"
)
FIXED_ARVAN_S3_IR_RECEIVER_CREDENTIAL_FILE = Path(
    "/etc/trading-bot/security/arvan-s3-ir-receiver-credentials.json"
)

ARVAN_S3_SEPARATED_CREDENTIAL_MAX_BYTES = 16 * 1024
ARVAN_S3_SEPARATED_CREDENTIAL_MAX_VALUE_BYTES = 1024

_SOURCE_SITE = "webapp_fi"
_DESTINATION_SITE = "webapp_ir"
_FI_ROLE = _role_profiles.ARVAN_S3_FI_PUBLISHER_ROLE
_IR_ROLE = _role_profiles.ARVAN_S3_IR_RECEIVER_ROLE
_FI_ACTION_PROFILE = _role_profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE
_IR_ACTION_PROFILE = _role_profiles.ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE
_IDENTITY_FINGERPRINT_DOMAIN = b"gold-trade-arvan-s3-machine-user-identity-v1\x00"
_CREDENTIAL_VALUE_RE = re.compile(r"^[\x21-\x7e]{1,1024}$", re.ASCII)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_PAIR_CAPABILITY = object()

# This is the exact operation surface expected by the existing injected
# `physical_arvan_s3_immutability_live_probe`, not a claim that a file can
# prove provider-side IAM policy.  The later live probe still exercises both
# the allowed calls and the explicitly denied calls.
ARVAN_S3_FI_PUBLISHER_EXPECTED_PROBE_ACTIONS = (
    _role_profiles.ARVAN_S3_FI_PUBLISHER_EXPECTED_ACTIONS
)
ARVAN_S3_IR_RECEIVER_EXPECTED_PROBE_ACTIONS = (
    _role_profiles.ARVAN_S3_IR_RECEIVER_EXPECTED_ACTIONS
)


class ArvanS3SeparatedCredentialLoaderError(ValueError):
    """Fixed-code refusal that never echoes credential material or a path."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedArvanS3SeparatedCredentialLoaderConfig:
    """Non-secret, default-off route and action policy for the two fixed files."""

    schema: str = PHYSICAL_ARVAN_S3_SEPARATED_CREDENTIAL_LOADER_SCHEMA
    endpoint: str = ""
    region: str = ""
    bucket: str = ""
    enabled: bool = PHYSICAL_ARVAN_S3_SEPARATED_CREDENTIAL_LOADER_DEFAULT_ENABLED
    source_site: str = _SOURCE_SITE
    destination_site: str = _DESTINATION_SITE
    fi_publisher_action_profile: str = _FI_ACTION_PROFILE
    ir_receiver_action_profile: str = _IR_ACTION_PROFILE
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class VerifiedArvanS3SeparatedCredentialPair:
    """Opaque public admission result; it deliberately contains no key material."""

    schema: str
    endpoint: str
    region: str
    bucket: str
    source_site: str
    destination_site: str
    fi_publisher_identity_sha256: str
    ir_receiver_identity_sha256: str
    fi_publisher_action_profile: str
    ir_receiver_action_profile: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_SEPARATED_CREDENTIAL_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class ArvanS3ImmutabilityProbeCredentialProjection:
    """Public mapping for two `PhysicalArvanS3ImmutabilityScopedClient` values."""

    schema: str
    endpoint: str
    region: str
    bucket: str
    source_site: str
    destination_site: str
    fi_publisher_role: str
    fi_publisher_identity_sha256: str
    fi_publisher_allowed_operations: tuple[str, ...]
    ir_receiver_role: str
    ir_receiver_identity_sha256: str
    ir_receiver_allowed_operations: tuple[str, ...]


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
        raise TypeError("ARVAN_S3_PRIVATE_CREDENTIAL_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _LoadedSeparatedCredentialFacts:
    config: _ConfigFacts
    fi_publisher: _CredentialFacts = field(repr=False, compare=False)
    ir_receiver: _CredentialFacts = field(repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_PRIVATE_CREDENTIAL_SERIALIZATION_FORBIDDEN")


def _fail(code: str) -> None:
    raise ArvanS3SeparatedCredentialLoaderError(code)


def _require_root() -> None:
    if os.geteuid() != 0:
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_ROOT_REQUIRED")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    del value
    _fail("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID")


def _credential_value(value: object) -> str:
    if (
        type(value) is not str
        or _CREDENTIAL_VALUE_RE.fullmatch(value) is None
        or len(value.encode("ascii", "strict")) > ARVAN_S3_SEPARATED_CREDENTIAL_MAX_VALUE_BYTES
    ):
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID")
    return value


def _identity_sha256(access_key: str) -> str:
    # The access-key identifier never becomes output.  This domain-separated
    # digest is the only identity value released to the probe wiring.
    return hashlib.sha256(_IDENTITY_FINGERPRINT_DOMAIN + access_key.encode("ascii")).hexdigest()


def _fixed_private_path(path: object) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID")
    parent = path.parent
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
        parent_metadata = os.lstat(parent)
        parent_resolved = parent.resolve(strict=True)
    except OSError:
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID")
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
        or metadata.st_size > ARVAN_S3_SEPARATED_CREDENTIAL_MAX_BYTES
        or not hasattr(os, "O_NOFOLLOW")
    ):
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID")
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
            or metadata.st_size > ARVAN_S3_SEPARATED_CREDENTIAL_MAX_BYTES
            or before.st_dev != metadata.st_dev
            or before.st_ino != metadata.st_ino
        ):
            _fail("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 4096)
            if type(chunk) is not bytes:
                _fail("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID")
            if not chunk:
                break
            total += len(chunk)
            if total > ARVAN_S3_SEPARATED_CREDENTIAL_MAX_BYTES:
                _fail("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID")
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
            _fail("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID")
        return b"".join(chunks), metadata.st_dev, metadata.st_ino
    except ArvanS3SeparatedCredentialLoaderError:
        raise
    except OSError:
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                _fail("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID")


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
    except ArvanS3SeparatedCredentialLoaderError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID")
    if type(value) is not dict or set(value) != {
        "schema",
        "role",
        "action_profile",
        "access_key",
        "secret_key",
    }:
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_INVALID")
    if (
        value.get("role") == _FI_ROLE
        and value.get("action_profile")
        == _role_profiles.ARVAN_S3_LEGACY_FI_PUBLISHER_IMMUTABLE_PREFLIGHT_PROFILE
    ):
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_LEGACY_PROFILE_MIGRATION_REQUIRED")
    if (
        value["schema"] != PHYSICAL_ARVAN_S3_MACHINE_USER_CREDENTIAL_SCHEMA
        or value["role"] != expected_role
        or value["action_profile"] != expected_action_profile
    ):
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_SCOPE_INVALID")
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
    if type(config) is not RootOwnedArvanS3SeparatedCredentialLoaderConfig:
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_CONFIG_INVALID")
    if config.schema != PHYSICAL_ARVAN_S3_SEPARATED_CREDENTIAL_LOADER_SCHEMA:
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_CONFIG_INVALID")
    if type(config.enabled) is not bool:
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_CONFIG_INVALID")
    if require_enabled and config.enabled is not True:
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_LOADER_DISABLED")
    try:
        _role_profiles.require_canonical_arvan_s3_role_profile(
            role=_FI_ROLE,
            action_profile=config.fi_publisher_action_profile,
        )
        _role_profiles.require_canonical_arvan_s3_role_profile(
            role=_IR_ROLE,
            action_profile=config.ir_receiver_action_profile,
        )
    except _role_profiles.ArvanS3RoleProfileError as exc:
        if exc.code == "ARVAN_S3_ROLE_PROFILE_LEGACY_MIGRATION_REQUIRED":
            _fail("ARVAN_S3_SEPARATED_CREDENTIAL_LEGACY_PROFILE_MIGRATION_REQUIRED")
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_ROUTE_OR_ACTION_INVALID")
    if (
        config.source_site != _SOURCE_SITE
        or config.destination_site != _DESTINATION_SITE
        or config.fi_publisher_action_profile != _FI_ACTION_PROFILE
        or config.ir_receiver_action_profile != _IR_ACTION_PROFILE
        or config.direct_site_control != "forbidden"
        or config.destination_object_ingest != "pull-only"
    ):
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_ROUTE_OR_ACTION_INVALID")
    try:
        factory_config = validate_root_owned_arvan_s3_client_factory_config(
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
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_CONFIG_INVALID")
    return _ConfigFacts(
        endpoint=factory_config.endpoint,
        region=factory_config.region,
        bucket=factory_config.bucket,
    )


def validate_root_owned_arvan_s3_separated_credential_loader_config(
    config: RootOwnedArvanS3SeparatedCredentialLoaderConfig,
) -> RootOwnedArvanS3SeparatedCredentialLoaderConfig:
    """Pure validation; it never reads either credential file."""

    facts = _config_facts(config, require_enabled=False)
    return RootOwnedArvanS3SeparatedCredentialLoaderConfig(
        schema=PHYSICAL_ARVAN_S3_SEPARATED_CREDENTIAL_LOADER_SCHEMA,
        endpoint=facts.endpoint,
        region=facts.region,
        bucket=facts.bucket,
        enabled=config.enabled,
        source_site=_SOURCE_SITE,
        destination_site=_DESTINATION_SITE,
        fi_publisher_action_profile=_FI_ACTION_PROFILE,
        ir_receiver_action_profile=_IR_ACTION_PROFILE,
        direct_site_control="forbidden",
        destination_object_ingest="pull-only",
    )


def _load_root_owned_separated_credential_facts(
    config: RootOwnedArvanS3SeparatedCredentialLoaderConfig,
) -> _LoadedSeparatedCredentialFacts:
    """Internal short-lived key-material handoff for the paired client seam.

    This private helper must never be exported, projected, serialized, or
    logged.  Its credential values exist only while an in-process, separately
    reviewed root factory constructs its two independently scoped clients.
    """

    facts = _config_facts(config, require_enabled=True)
    _require_root()
    fi_path = FIXED_ARVAN_S3_FI_PUBLISHER_CREDENTIAL_FILE
    ir_path = FIXED_ARVAN_S3_IR_RECEIVER_CREDENTIAL_FILE
    if fi_path == ir_path:
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_FILE_COLLISION")
    fi = _load_credential(
        fi_path,
        expected_role=_FI_ROLE,
        expected_action_profile=_FI_ACTION_PROFILE,
    )
    ir = _load_credential(
        ir_path,
        expected_role=_IR_ROLE,
        expected_action_profile=_IR_ACTION_PROFILE,
    )
    if (
        (fi.device, fi.inode) == (ir.device, ir.inode)
        or fi.identity_sha256 == ir.identity_sha256
        or fi.access_key == ir.access_key
        or fi.secret_key == ir.secret_key
    ):
        _fail("ARVAN_S3_SEPARATED_CREDENTIALS_NOT_SEPARATE")
    return _LoadedSeparatedCredentialFacts(
        config=facts,
        fi_publisher=fi,
        ir_receiver=ir,
    )


def _load_root_owned_ir_receiver_credential_facts(
    config: RootOwnedArvanS3SeparatedCredentialLoaderConfig,
) -> tuple[_ConfigFacts, _CredentialFacts]:
    """Open only the WA-IR receiver secret for a receiver-local exact pull.

    This intentionally remains private to a separately reviewed receiver-side
    credential-to-client boundary.  The normal paired preflight still uses
    :func:`_load_root_owned_separated_credential_facts` to prove the two
    machine users differ.  A WA-IR host must not receive the FI publisher
    secret merely to perform one exact private Object-Storage GET; this helper
    therefore validates the same fixed route/action policy and opens only the
    fixed IR credential file.  Its caller must independently pin a distinct
    FI identity fingerprint alongside the admitted IR identity.
    """

    facts = _config_facts(config, require_enabled=True)
    _require_root()
    ir = _load_credential(
        FIXED_ARVAN_S3_IR_RECEIVER_CREDENTIAL_FILE,
        expected_role=_IR_ROLE,
        expected_action_profile=_IR_ACTION_PROFILE,
    )
    return facts, ir


def _load_root_owned_fi_publisher_credential_facts(
    config: RootOwnedArvanS3SeparatedCredentialLoaderConfig,
) -> tuple[_ConfigFacts, _CredentialFacts]:
    """Open only the WA-FI publisher secret for one recovery handoff.

    This is intentionally private to the separately reviewed FI publisher
    client boundary.  The paired live preflight remains responsible for
    proving that the FI and IR identities are distinct.  A normal WA-FI
    recovery-material upload must not reopen the WA-IR receiver credential;
    its caller instead pins and rechecks the independently verified IR
    identity from that fresh preflight before it accepts this FI credential.

    The helper therefore cannot create a new route, choose a credential path,
    or widen the FI role.  It merely validates the same fixed route/action
    policy and opens the one fixed FI machine-user file.
    """

    facts = _config_facts(config, require_enabled=True)
    _require_root()
    fi = _load_credential(
        FIXED_ARVAN_S3_FI_PUBLISHER_CREDENTIAL_FILE,
        expected_role=_FI_ROLE,
        expected_action_profile=_FI_ACTION_PROFILE,
    )
    return facts, fi


def load_root_owned_arvan_s3_separated_credential_pair(
    config: RootOwnedArvanS3SeparatedCredentialLoaderConfig,
) -> VerifiedArvanS3SeparatedCredentialPair:
    """Legacy compatibility-only paired admission; never a production route.

    It remains solely for the historical two-client immutability probe.  No
    Full-Matrix runtime or four-role binder may consume it.  No SDK or client
    is constructed here.  The returned object deliberately
    retains only public identity fingerprints and fixed route/action facts.
    """

    loaded = _load_root_owned_separated_credential_facts(config)
    result = VerifiedArvanS3SeparatedCredentialPair(
        schema=PHYSICAL_ARVAN_S3_SEPARATED_CREDENTIAL_LOADER_SCHEMA,
        endpoint=loaded.config.endpoint,
        region=loaded.config.region,
        bucket=loaded.config.bucket,
        source_site=_SOURCE_SITE,
        destination_site=_DESTINATION_SITE,
        fi_publisher_identity_sha256=loaded.fi_publisher.identity_sha256,
        ir_receiver_identity_sha256=loaded.ir_receiver.identity_sha256,
        fi_publisher_action_profile=_FI_ACTION_PROFILE,
        ir_receiver_action_profile=_IR_ACTION_PROFILE,
    )
    object.__setattr__(result, "_capability", _PAIR_CAPABILITY)
    return result


def _pair_facts(
    value: object,
    *,
    config: RootOwnedArvanS3SeparatedCredentialLoaderConfig,
) -> tuple[VerifiedArvanS3SeparatedCredentialPair, _ConfigFacts]:
    facts = _config_facts(config, require_enabled=True)
    _require_root()
    if (
        type(value) is not VerifiedArvanS3SeparatedCredentialPair
        or value._capability is not _PAIR_CAPABILITY
    ):
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_PAIR_REQUIRED")
    if (
        value.schema != PHYSICAL_ARVAN_S3_SEPARATED_CREDENTIAL_LOADER_SCHEMA
        or value.endpoint != facts.endpoint
        or value.region != facts.region
        or value.bucket != facts.bucket
        or value.source_site != _SOURCE_SITE
        or value.destination_site != _DESTINATION_SITE
        or value.fi_publisher_action_profile != _FI_ACTION_PROFILE
        or value.ir_receiver_action_profile != _IR_ACTION_PROFILE
        or type(value.fi_publisher_identity_sha256) is not str
        or type(value.ir_receiver_identity_sha256) is not str
        or _SHA256_RE.fullmatch(value.fi_publisher_identity_sha256) is None
        or _SHA256_RE.fullmatch(value.ir_receiver_identity_sha256) is None
        or value.fi_publisher_identity_sha256 == "0" * 64
        or value.ir_receiver_identity_sha256 == "0" * 64
        or value.fi_publisher_identity_sha256 == value.ir_receiver_identity_sha256
    ):
        _fail("ARVAN_S3_SEPARATED_CREDENTIAL_PAIR_TAMPERED")
    return value, facts


def require_verified_arvan_s3_separated_credential_pair(
    value: object,
    *,
    config: RootOwnedArvanS3SeparatedCredentialLoaderConfig,
) -> VerifiedArvanS3SeparatedCredentialPair:
    """Revalidate an opaque public admission result without reopening files."""

    pair, _facts = _pair_facts(value, config=config)
    return pair


def project_root_owned_arvan_s3_immutability_probe_credentials(
    pair: VerifiedArvanS3SeparatedCredentialPair,
    *,
    config: RootOwnedArvanS3SeparatedCredentialLoaderConfig,
) -> ArvanS3ImmutabilityProbeCredentialProjection:
    """Return only the identities/actions needed to wire the injected live probe."""

    verified, facts = _pair_facts(pair, config=config)
    return ArvanS3ImmutabilityProbeCredentialProjection(
        schema=PHYSICAL_ARVAN_S3_SEPARATED_CREDENTIAL_PROJECTION_SCHEMA,
        endpoint=facts.endpoint,
        region=facts.region,
        bucket=facts.bucket,
        source_site=_SOURCE_SITE,
        destination_site=_DESTINATION_SITE,
        fi_publisher_role=_FI_ROLE,
        fi_publisher_identity_sha256=verified.fi_publisher_identity_sha256,
        fi_publisher_allowed_operations=ARVAN_S3_FI_PUBLISHER_EXPECTED_PROBE_ACTIONS,
        ir_receiver_role=_IR_ROLE,
        ir_receiver_identity_sha256=verified.ir_receiver_identity_sha256,
        ir_receiver_allowed_operations=ARVAN_S3_IR_RECEIVER_EXPECTED_PROBE_ACTIONS,
    )
