"""Root-only WA-IR receiver for FI-signed preflight-request provisioning.

This is the receiver half of one narrow one-way route:

``FI signed payload -> age ciphertext in private versioned storage -> WA-IR``.

It reads a root-pinned, FI-signed redacted locator, GETs exactly its Key and
VersionId with only the WA-IR receiver machine user, decrypts using only its
dedicated age identity, verifies the signed plaintext and all bindings, then
atomically replaces the fixed request consumed by the WA-IR attester.  It has
no list, HEAD, PUT, delete, mutable selector, direct FI connection, or
controller/Writer capability.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Protocol

from core import dedicated_host_preflight_ir_request_provisioning as _protocol
from core import dedicated_host_preflight_ir_witness_attestation as _attestation
from core import dedicated_host_preflight_ir_witness_attestation_runtime as _attester_runtime
from core import physical_arvan_immutability_preflight as _preflight
from core import physical_arvan_s3_ir_receiver_role_loader as _ir_receiver_loader
from core import physical_arvan_s3_role_local_credential_reader as _credential_reader
from core import physical_age_v1_adapter as _age
from core.dedicated_host_preflight_receipt import canonical_json_bytes
from core.physical_arvan_exact_version_pull import (
    ArvanExactVersionPullExpectation,
    ArvanExactVersionPullReader,
    RootOwnedArvanExactVersionPullConfig,
    validate_arvan_exact_version_pull_config,
)
from core.physical_age_v1_adapter import PhysicalAgeV1Decryptor, PhysicalAgeV1DecryptorConfig
from core.physical_arvan_s3_role_local_route_policy import ArvanS3RoleLocalRoutePolicy
from core.physical_wal_object_manifest import PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE


__all__ = (
    "DEDICATED_HOST_PREFLIGHT_IR_REQUEST_PROVISIONING_DEFAULT_ENABLED",
    "FIXED_WA_IR_REQUEST_PROVISIONING_AGE_IDENTITY_FILE",
    "FIXED_WA_IR_REQUEST_PROVISIONING_AGE_WORKSPACE_ROOT",
    "FIXED_WA_IR_REQUEST_PROVISIONING_LOCATOR_FILE",
    "FIXED_WA_IR_REQUEST_PROVISIONING_REPLAY_STATE_ROOT",
    "DedicatedHostPreflightIrRequestProvisioningRuntimeError",
    "RootOwnedWaIrPreflightRequestProvisioningReceiver",
    "RootOwnedWaIrPreflightRequestProvisioningReceiverConfig",
    "WaIrPreflightRequestInstallation",
    "validate_root_owned_wa_ir_preflight_request_provisioning_receiver_config",
)


DEDICATED_HOST_PREFLIGHT_IR_REQUEST_PROVISIONING_DEFAULT_ENABLED = False

FIXED_WA_IR_REQUEST_PROVISIONING_LOCATOR_FILE = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/wa-ir-witness-attestation-request-locator.json"
)
FIXED_WA_IR_REQUEST_PROVISIONING_AGE_IDENTITY_FILE = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/wa-ir-preflight-request-age-identity.txt"
)
FIXED_WA_IR_REQUEST_PROVISIONING_AGE_WORKSPACE_ROOT = Path(
    "/var/lib/trading-bot/dedicated-host-preflight/wa-ir-preflight-request-age"
)
FIXED_WA_IR_REQUEST_PROVISIONING_REPLAY_STATE_ROOT = Path(
    "/var/lib/trading-bot/dedicated-host-preflight/wa-ir-preflight-request-replay"
)

_SCHEMA = "three-site-dedicated-host-preflight-wa-ir-request-provisioning-receiver-v1"
_MODE = "root-owned-wa-ir-exact-version-age-v1-atomic-request-install-v1"
_SOURCE_SITE = "webapp_fi"
_DESTINATION_SITE = "webapp_ir"
_STATE_SCHEMA = "three-site-dedicated-host-preflight-wa-ir-request-provisioning-replay-v1"
_STATE_FILE = "replay-state.json"
_LOCK_FILE = "replay.lock"
_MAX_REPLAY_ENTRIES = 64
_MAX_STATE_BYTES = 64 * 1024
_MAX_LOCATOR_BYTES = _protocol.MAX_FI_WA_IR_PREFLIGHT_REQUEST_LOCATOR_BYTES
_MAX_REQUEST_BYTES = _attestation.MAX_WA_IR_WITNESS_ATTESTATION_REQUEST_BYTES


class DedicatedHostPreflightIrRequestProvisioningRuntimeError(ValueError):
    """A redacted failure from the isolated WA-IR receiver runtime."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedWaIrPreflightRequestProvisioningReceiverConfig:
    """Default-off receiver policy with no selectable secret or destination path."""

    schema: str = _SCHEMA
    exact_pull_config: RootOwnedArvanExactVersionPullConfig | None = field(
        default=None, repr=False, compare=False
    )
    age_decryptor_config: PhysicalAgeV1DecryptorConfig | None = field(
        default=None, repr=False, compare=False
    )
    preflight: _preflight.VerifiedPhysicalArvanImmutabilityPreflight | None = field(
        default=None, repr=False, compare=False
    )
    expected_fi_request_signer_public_key: bytes = b""
    expected_locator_sha256: str = ""
    enabled: bool = DEDICATED_HOST_PREFLIGHT_IR_REQUEST_PROVISIONING_DEFAULT_ENABLED
    source_site: str = _SOURCE_SITE
    destination_site: str = _DESTINATION_SITE
    mode: str = _MODE
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class WaIrPreflightRequestInstallation:
    """Non-secret evidence of a one-time local request installation."""

    locator_sha256: str
    payload_sha256: str
    request_sha256: str
    attestation_id: str
    nonce: str


@dataclass(frozen=True)
class _Facts:
    pull_config: RootOwnedArvanExactVersionPullConfig
    age_config: PhysicalAgeV1DecryptorConfig
    preflight: _preflight.VerifiedPhysicalArvanImmutabilityPreflight
    fi_public_key: bytes
    expected_locator_sha256: str
    fi_identity_sha256: str
    ir_identity_sha256: str


class _RawS3ClientBuilder(Protocol):
    def __call__(self, *, endpoint: str, region: str, access_key: str, secret_key: str) -> object: ...


CredentialAdmitter = Callable[
    [ArvanS3RoleLocalRoutePolicy],
    tuple[
        _credential_reader.ArvanS3RoleLocalRouteFacts,
        _credential_reader.ArvanS3RoleLocalCredentialFacts,
    ],
]
AgeDecryptorFactory = Callable[[PhysicalAgeV1DecryptorConfig], object]


def _fail(code: str) -> None:
    raise DedicatedHostPreflightIrRequestProvisioningRuntimeError(code)


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("WA_IR_REQUEST_PROVISIONING_ROOT_REQUIRED")
    except OSError:
        _fail("WA_IR_REQUEST_PROVISIONING_ROOT_REQUIRED")


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _sha256(value: object, *, code: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or value == "0" * 64
        or any(item not in "0123456789abcdef" for item in value)
    ):
        _fail(code)
    return value


def _public_key(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        _fail(code)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, TypeError, ValueError):
        _fail(code)
    return value


def _private_directory(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        _fail(code)
    try:
        metadata = os.lstat(value)
        resolved = value.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != value
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail(code)
    return resolved


def _private_file(value: object, *, exact_mode: int, maximum: int, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts or not hasattr(os, "O_NOFOLLOW"):
        _fail(code)
    _private_directory(value.parent, code=code)
    descriptor = -1
    try:
        before = os.lstat(value)
        resolved = value.resolve(strict=True)
        descriptor = os.open(value, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        metadata = os.fstat(descriptor)
        if (
            resolved != value
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != exact_mode
            or metadata.st_dev != before.st_dev
            or metadata.st_ino != before.st_ino
            or not 1 <= metadata.st_size <= maximum
        ):
            _fail(code)
        return resolved
    except DedicatedHostPreflightIrRequestProvisioningRuntimeError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_private_file(path: Path, *, exact_mode: int, maximum: int, code: str) -> bytes:
    _private_file(path, exact_mode=exact_mode, maximum=maximum, code=code)
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        metadata = os.fstat(descriptor)
        result = bytearray()
        while len(result) < metadata.st_size:
            chunk = os.read(descriptor, metadata.st_size - len(result))
            if not chunk:
                _fail(code)
            result.extend(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        return bytes(result)
    except DedicatedHostPreflightIrRequestProvisioningRuntimeError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _facts(value: object, *, now: datetime, require_enabled: bool) -> _Facts:
    if type(value) is not RootOwnedWaIrPreflightRequestProvisioningReceiverConfig:
        _fail("WA_IR_REQUEST_PROVISIONING_CONFIG_INVALID")
    if (
        value.schema != _SCHEMA
        or type(value.enabled) is not bool
        or value.source_site != _SOURCE_SITE
        or value.destination_site != _DESTINATION_SITE
        or value.mode != _MODE
        or value.direct_site_control != "forbidden"
        or value.destination_object_ingest != "pull-only"
        or type(value.exact_pull_config) is not RootOwnedArvanExactVersionPullConfig
        or type(value.age_decryptor_config) is not PhysicalAgeV1DecryptorConfig
        or type(value.preflight) is not _preflight.VerifiedPhysicalArvanImmutabilityPreflight
    ):
        _fail("WA_IR_REQUEST_PROVISIONING_CONFIG_INVALID")
    if require_enabled and value.enabled is not True:
        _fail("WA_IR_REQUEST_PROVISIONING_DISABLED")
    try:
        pull_config = validate_arvan_exact_version_pull_config(value.exact_pull_config)
    except Exception:
        _fail("WA_IR_REQUEST_PROVISIONING_CONFIG_INVALID")
    if require_enabled and pull_config.enabled is not True:
        _fail("WA_IR_REQUEST_PROVISIONING_DISABLED")
    age_config = value.age_decryptor_config
    if (
        age_config.enabled is not True
        or age_config.direct_site_control != "forbidden"
        or age_config.destination_object_ingest != "pull-only"
        or age_config.identity_path != FIXED_WA_IR_REQUEST_PROVISIONING_AGE_IDENTITY_FILE
        or age_config.workspace_root != FIXED_WA_IR_REQUEST_PROVISIONING_AGE_WORKSPACE_ROOT
        or type(age_config.recipient) is not str
        or not age_config.recipient
        or type(age_config.maximum_plaintext_bytes) is not int
        or type(age_config.maximum_ciphertext_bytes) is not int
        or not 1 <= age_config.maximum_plaintext_bytes <= _protocol.MAX_FI_WA_IR_PREFLIGHT_REQUEST_PAYLOAD_BYTES
        or not age_config.maximum_plaintext_bytes <= age_config.maximum_ciphertext_bytes <= _protocol.MAX_FI_WA_IR_PREFLIGHT_REQUEST_CIPHERTEXT_BYTES
    ):
        _fail("WA_IR_REQUEST_PROVISIONING_AGE_CONFIG_INVALID")
    _private_directory(age_config.workspace_root, code="WA_IR_REQUEST_PROVISIONING_AGE_CONFIG_INVALID")
    _private_file(
        age_config.identity_path,
        exact_mode=0o400,
        maximum=_age.MAX_PHYSICAL_AGE_IDENTITY_BYTES,
        code="WA_IR_REQUEST_PROVISIONING_AGE_CONFIG_INVALID",
    )
    if pull_config.maximum_ciphertext_bytes < age_config.maximum_ciphertext_bytes:
        _fail("WA_IR_REQUEST_PROVISIONING_AGE_CONFIG_INVALID")
    try:
        preflight = _preflight.require_verified_physical_arvan_immutability_preflight(
            value.preflight,
            binding=value.preflight.binding,
            now=now,
        )
        restrictions = {item.role: item for item in preflight.observation.credential_restrictions}
        fi_identity = restrictions["fi-publisher"].credential_identity_sha256
        ir_identity = restrictions["ir-receiver"].credential_identity_sha256
    except Exception:
        _fail("WA_IR_REQUEST_PROVISIONING_PREFLIGHT_INVALID")
    binding = preflight.binding
    if (
        binding.source_site != _SOURCE_SITE
        or binding.destination_site != _DESTINATION_SITE
        or binding.endpoint != pull_config.endpoint
        or binding.region != pull_config.region
        or binding.bucket != pull_config.bucket
        or type(fi_identity) is not str
        or type(ir_identity) is not str
        or len(fi_identity) != 64
        or len(ir_identity) != 64
        or fi_identity == ir_identity
    ):
        _fail("WA_IR_REQUEST_PROVISIONING_PREFLIGHT_ROUTE_INVALID")
    return _Facts(
        pull_config=pull_config,
        age_config=age_config,
        preflight=preflight,
        fi_public_key=_public_key(
            value.expected_fi_request_signer_public_key,
            code="WA_IR_REQUEST_PROVISIONING_SIGNER_PIN_INVALID",
        ),
        expected_locator_sha256=_sha256(
            value.expected_locator_sha256,
            code="WA_IR_REQUEST_PROVISIONING_LOCATOR_PIN_INVALID",
        ),
        fi_identity_sha256=fi_identity,
        ir_identity_sha256=ir_identity,
    )


def validate_root_owned_wa_ir_preflight_request_provisioning_receiver_config(
    config: RootOwnedWaIrPreflightRequestProvisioningReceiverConfig,
) -> RootOwnedWaIrPreflightRequestProvisioningReceiverConfig:
    """Validate only shape and static pins; no filesystem or provider action."""

    if type(config) is not RootOwnedWaIrPreflightRequestProvisioningReceiverConfig:
        _fail("WA_IR_REQUEST_PROVISIONING_CONFIG_INVALID")
    if (
        config.schema != _SCHEMA
        or type(config.enabled) is not bool
        or config.source_site != _SOURCE_SITE
        or config.destination_site != _DESTINATION_SITE
        or config.mode != _MODE
        or config.direct_site_control != "forbidden"
        or config.destination_object_ingest != "pull-only"
        or type(config.exact_pull_config) is not RootOwnedArvanExactVersionPullConfig
        or type(config.age_decryptor_config) is not PhysicalAgeV1DecryptorConfig
        or type(config.preflight) is not _preflight.VerifiedPhysicalArvanImmutabilityPreflight
    ):
        _fail("WA_IR_REQUEST_PROVISIONING_CONFIG_INVALID")
    try:
        validate_arvan_exact_version_pull_config(config.exact_pull_config)
    except Exception:
        _fail("WA_IR_REQUEST_PROVISIONING_CONFIG_INVALID")
    _public_key(config.expected_fi_request_signer_public_key, code="WA_IR_REQUEST_PROVISIONING_SIGNER_PIN_INVALID")
    _sha256(config.expected_locator_sha256, code="WA_IR_REQUEST_PROVISIONING_LOCATOR_PIN_INVALID")
    return config


def _role_local_route_policy(facts: _Facts) -> ArvanS3RoleLocalRoutePolicy:
    """Construct the sole non-secret normal route accepted by IR artifact."""

    return ArvanS3RoleLocalRoutePolicy(
        endpoint=facts.pull_config.endpoint,
        region=facts.pull_config.region,
        bucket=facts.pull_config.bucket,
        enabled=True,
        source_site=_SOURCE_SITE,
        destination_site=_DESTINATION_SITE,
        object_storage_namespace=PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
        direct_site_control="forbidden",
        destination_object_ingest="pull-only",
    )


def _default_credential_admitter(
    route_policy: ArvanS3RoleLocalRoutePolicy,
) -> tuple[
    _credential_reader.ArvanS3RoleLocalRouteFacts,
    _credential_reader.ArvanS3RoleLocalCredentialFacts,
]:
    return _ir_receiver_loader.load_root_owned_arvan_s3_ir_receiver_role_credential_facts(route_policy)


def _default_raw_s3_client_builder(*, endpoint: str, region: str, access_key: str, secret_key: str) -> object:
    try:
        boto3_module = importlib.import_module("boto3")
        botocore_config_module = importlib.import_module("botocore.config")
        session_type = getattr(getattr(boto3_module, "session"), "Session")
        config_type = getattr(botocore_config_module, "Config")
        config = config_type(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=60,
            retries={"max_attempts": 2, "mode": "standard"},
            s3={"addressing_style": "path"},
            proxies={},
        )
        session = session_type(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        client = session.client(
            "s3", endpoint_url=endpoint, region_name=region, use_ssl=True, verify=True, config=config
        )
    except Exception:
        _fail("WA_IR_REQUEST_PROVISIONING_SDK_CLIENT_FAILED")
    if client is None:
        _fail("WA_IR_REQUEST_PROVISIONING_SDK_CLIENT_FAILED")
    return client


class _ReceiverExactGetClient:
    """Private receiver wrapper that exposes one already-pinned GET only."""

    def __init__(self, *, raw: object, bucket: str, object_key: str, version_id: str) -> None:
        self._raw = raw
        self._bucket = bucket
        self._object_key = object_key
        self._version_id = version_id

    def get_object(self, *, Bucket: str, Key: str, VersionId: str) -> Mapping[str, Any]:
        if (
            Bucket != self._bucket
            or Key != self._object_key
            or VersionId != self._version_id
        ):
            _fail("WA_IR_REQUEST_PROVISIONING_EXACT_GET_SELECTOR_INVALID")
        try:
            response = getattr(self._raw, "get_object")(Bucket=Bucket, Key=Key, VersionId=VersionId)
        except Exception:
            _fail("WA_IR_REQUEST_PROVISIONING_EXACT_GET_FAILED")
        if not isinstance(response, Mapping):
            _fail("WA_IR_REQUEST_PROVISIONING_EXACT_GET_FAILED")
        return dict(response)


def _state_root() -> Path:
    root = FIXED_WA_IR_REQUEST_PROVISIONING_REPLAY_STATE_ROOT
    if not isinstance(root, Path) or not root.is_absolute() or ".." in root.parts:
        _fail("WA_IR_REQUEST_PROVISIONING_REPLAY_ROOT_UNSAFE")
    parent = root.parent
    _private_directory(parent, code="WA_IR_REQUEST_PROVISIONING_REPLAY_ROOT_UNSAFE")
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        _fail("WA_IR_REQUEST_PROVISIONING_REPLAY_ROOT_UNSAFE")
    return _private_directory(root, code="WA_IR_REQUEST_PROVISIONING_REPLAY_ROOT_UNSAFE")


def _state_entry(value: object, *, code: str) -> dict[str, str]:
    if type(value) is not dict or set(value) != {
        "locator_sha256", "payload_sha256", "request_sha256", "attestation_id", "nonce"
    }:
        _fail(code)
    try:
        # The protocol parser is deliberately the single grammar owner for
        # UUID/nonce request values, so use a small canonical request only to
        # validate these two fields indirectly at installation time.  State
        # itself retains no request body and must at least keep bounded ASCII.
        attestation_id = value["attestation_id"]
        nonce = value["nonce"]
        if (
            type(attestation_id) is not str
            or len(attestation_id) != 36
            or type(nonce) is not str
            or not 22 <= len(nonce) <= 128
            or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for character in nonce)
        ):
            _fail(code)
    except DedicatedHostPreflightIrRequestProvisioningRuntimeError:
        raise
    except Exception:
        _fail(code)
    return {
        "locator_sha256": _sha256(value["locator_sha256"], code=code),
        "payload_sha256": _sha256(value["payload_sha256"], code=code),
        "request_sha256": _sha256(value["request_sha256"], code=code),
        "attestation_id": attestation_id,
        "nonce": nonce,
    }


@contextmanager
def _locked_replay_state() -> Iterator[tuple[Path, dict[str, Any]]]:
    root = _state_root()
    lock = root / _LOCK_FILE
    descriptor = -1
    try:
        descriptor = os.open(
            lock,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("WA_IR_REQUEST_PROVISIONING_REPLAY_STATE_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        state_path = root / _STATE_FILE
        if not state_path.exists():
            state: dict[str, Any] = {"schema": _STATE_SCHEMA, "entries": []}
        else:
            raw = _read_private_file(
                state_path,
                exact_mode=0o600,
                maximum=_MAX_STATE_BYTES,
                code="WA_IR_REQUEST_PROVISIONING_REPLAY_STATE_UNSAFE",
            )
            try:
                state = json.loads(raw.decode("ascii", "strict"))
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                _fail("WA_IR_REQUEST_PROVISIONING_REPLAY_STATE_INVALID")
            if (
                type(state) is not dict
                or set(state) != {"schema", "entries"}
                or state.get("schema") != _STATE_SCHEMA
                or type(state.get("entries")) is not list
                or raw != canonical_json_bytes(state) + b"\n"
                or len(state["entries"]) > _MAX_REPLAY_ENTRIES
            ):
                _fail("WA_IR_REQUEST_PROVISIONING_REPLAY_STATE_INVALID")
            state = {
                "schema": _STATE_SCHEMA,
                "entries": [
                    _state_entry(item, code="WA_IR_REQUEST_PROVISIONING_REPLAY_STATE_INVALID")
                    for item in state["entries"]
                ],
            }
        yield root, state
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _write_state(root: Path, state: dict[str, Any]) -> None:
    if (
        type(state) is not dict
        or set(state) != {"schema", "entries"}
        or state.get("schema") != _STATE_SCHEMA
        or type(state.get("entries")) is not list
        or not 1 <= len(state["entries"]) <= _MAX_REPLAY_ENTRIES
    ):
        _fail("WA_IR_REQUEST_PROVISIONING_REPLAY_STATE_INVALID")
    state = {
        "schema": _STATE_SCHEMA,
        "entries": [
            _state_entry(item, code="WA_IR_REQUEST_PROVISIONING_REPLAY_STATE_INVALID")
            for item in state["entries"]
        ],
    }
    payload = canonical_json_bytes(state) + b"\n"
    path = root / _STATE_FILE
    temporary = root / ".replay-state.new"
    descriptor = -1
    created = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        created = True
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail("WA_IR_REQUEST_PROVISIONING_REPLAY_STATE_WRITE_FAILED")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except DedicatedHostPreflightIrRequestProvisioningRuntimeError:
        raise
    except OSError:
        _fail("WA_IR_REQUEST_PROVISIONING_REPLAY_STATE_WRITE_FAILED")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if created:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _install_request(payload: bytes) -> None:
    path = _attester_runtime.FIXED_WA_IR_WITNESS_ATTESTATION_REQUEST_FILE
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail("WA_IR_REQUEST_PROVISIONING_REQUEST_DESTINATION_UNSAFE")
    parent = _private_directory(path.parent, code="WA_IR_REQUEST_PROVISIONING_REQUEST_DESTINATION_UNSAFE")
    temporary = parent / ("." + path.name + ".new")
    descriptor = -1
    created = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        created = True
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail("WA_IR_REQUEST_PROVISIONING_REQUEST_INSTALL_FAILED")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except DedicatedHostPreflightIrRequestProvisioningRuntimeError:
        raise
    except OSError:
        _fail("WA_IR_REQUEST_PROVISIONING_REQUEST_INSTALL_FAILED")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if created:
            try:
                os.unlink(temporary)
            except OSError:
                pass


class RootOwnedWaIrPreflightRequestProvisioningReceiver:
    """Inert construction plus one root-only exact pull/decrypt/install action."""

    def __init__(
        self,
        config: RootOwnedWaIrPreflightRequestProvisioningReceiverConfig,
        *,
        clock: Callable[[], datetime] | None,
        credential_admitter: CredentialAdmitter = _default_credential_admitter,
        raw_s3_client_builder: _RawS3ClientBuilder = _default_raw_s3_client_builder,
        age_decryptor_factory: AgeDecryptorFactory | None = None,
    ) -> None:
        self._config = validate_root_owned_wa_ir_preflight_request_provisioning_receiver_config(config)
        self._clock = clock
        self._credential_admitter = credential_admitter
        self._raw_s3_client_builder = raw_s3_client_builder
        self._age_decryptor_factory = age_decryptor_factory

    def _now(self) -> datetime:
        if self._clock is None or not callable(self._clock):
            _fail("WA_IR_REQUEST_PROVISIONING_CLOCK_REQUIRED")
        try:
            return _utc(self._clock(), code="WA_IR_REQUEST_PROVISIONING_CLOCK_INVALID")
        except DedicatedHostPreflightIrRequestProvisioningRuntimeError:
            raise
        except Exception:
            _fail("WA_IR_REQUEST_PROVISIONING_CLOCK_INVALID")

    def _new_client(self, facts: _Facts, locator: _protocol.VerifiedFiWaIrPreflightRequestLocator) -> _ReceiverExactGetClient:
        if not callable(self._credential_admitter) or not callable(self._raw_s3_client_builder):
            _fail("WA_IR_REQUEST_PROVISIONING_CLIENT_FACTORY_INVALID")
        try:
            route, credential = self._credential_admitter(_role_local_route_policy(facts))
        except Exception:
            _fail("WA_IR_REQUEST_PROVISIONING_IR_CREDENTIAL_ADMISSION_FAILED")
        if (
            type(route) is not _credential_reader.ArvanS3RoleLocalRouteFacts
            or type(credential) is not _credential_reader.ArvanS3RoleLocalCredentialFacts
            or route.endpoint != facts.pull_config.endpoint
            or route.region != facts.pull_config.region
            or route.bucket != facts.pull_config.bucket
            or credential.identity_sha256 != facts.ir_identity_sha256
            or credential.identity_sha256 == facts.fi_identity_sha256
        ):
            _fail("WA_IR_REQUEST_PROVISIONING_IR_CREDENTIAL_MISMATCH")
        try:
            raw = self._raw_s3_client_builder(
                endpoint=route.endpoint,
                region=route.region,
                access_key=credential.access_key,
                secret_key=credential.secret_key,
            )
        except Exception:
            _fail("WA_IR_REQUEST_PROVISIONING_SDK_CLIENT_FAILED")
        if raw is None:
            _fail("WA_IR_REQUEST_PROVISIONING_SDK_CLIENT_FAILED")
        return _ReceiverExactGetClient(
            raw=raw,
            bucket=route.bucket,
            object_key=locator.object.object_key,
            version_id=locator.object.version_id,
        )

    def _new_decryptor(self, facts: _Facts) -> object:
        try:
            result = (
                PhysicalAgeV1Decryptor(facts.age_config)
                if self._age_decryptor_factory is None
                else self._age_decryptor_factory(facts.age_config)
            )
        except Exception:
            _fail("WA_IR_REQUEST_PROVISIONING_AGE_DECRYPTOR_INVALID")
        if not callable(getattr(result, "decrypt", None)):
            _fail("WA_IR_REQUEST_PROVISIONING_AGE_DECRYPTOR_INVALID")
        return result

    @staticmethod
    def _verify_locator_binding(
        locator: _protocol.VerifiedFiWaIrPreflightRequestLocator,
        facts: _Facts,
    ) -> None:
        binding = facts.preflight.binding
        if (
            locator.locator_sha256 != facts.expected_locator_sha256
            or locator.route_binding_sha256 != binding.route_binding_sha256
            or locator.fi_publisher_identity_sha256 != facts.fi_identity_sha256
            or locator.ir_receiver_identity_sha256 != facts.ir_identity_sha256
            or locator.age_recipient != facts.age_config.recipient
            or locator.campaign_id != binding.campaign_id
            or locator.release_sha != binding.release_sha
        ):
            _fail("WA_IR_REQUEST_PROVISIONING_LOCATOR_BINDING_MISMATCH")

    @staticmethod
    def _verify_payload_binding(
        payload: _protocol.VerifiedFiWaIrPreflightRequestPayload,
        locator: _protocol.VerifiedFiWaIrPreflightRequestLocator,
        facts: _Facts,
    ) -> None:
        request = payload.request
        if (
            payload.payload_sha256 != locator.payload_sha256
            or request.attestation_request_sha256 != locator.request_sha256
            or request.readonly_request["campaign_id"] != locator.campaign_id
            or request.readonly_request["operation_id"] != locator.operation_id
            or request.readonly_request["release_sha"] != locator.release_sha
            or request.readonly_request["manifest_sha256"] != locator.manifest_sha256
            or request.attestation_id != locator.attestation_id
            or request.nonce != locator.nonce
            or payload.route_binding_sha256 != locator.route_binding_sha256
            or payload.fi_publisher_identity_sha256 != locator.fi_publisher_identity_sha256
            or payload.ir_receiver_identity_sha256 != locator.ir_receiver_identity_sha256
            or payload.age_recipient != locator.age_recipient
            or payload.issued_at != locator.issued_at
            or payload.expires_at != locator.expires_at
            or request.readonly_request["campaign_id"] != facts.preflight.binding.campaign_id
            or request.readonly_request["release_sha"] != facts.preflight.binding.release_sha
        ):
            _fail("WA_IR_REQUEST_PROVISIONING_PAYLOAD_BINDING_MISMATCH")

    def install(self) -> WaIrPreflightRequestInstallation:
        """Consume exactly one root-pinned locator; no alternate request path exists."""

        _require_root()
        started = self._now()
        facts = _facts(self._config, now=started, require_enabled=True)
        locator_raw = _read_private_file(
            FIXED_WA_IR_REQUEST_PROVISIONING_LOCATOR_FILE,
            exact_mode=0o600,
            maximum=_MAX_LOCATOR_BYTES,
            code="WA_IR_REQUEST_PROVISIONING_LOCATOR_FILE_UNSAFE",
        )
        try:
            locator = _protocol.verify_fi_wa_ir_preflight_request_locator(
                canonical_locator=locator_raw,
                expected_fi_public_key=facts.fi_public_key,
                now=started,
            )
        except Exception:
            _fail("WA_IR_REQUEST_PROVISIONING_LOCATOR_REJECTED")
        self._verify_locator_binding(locator, facts)
        with _locked_replay_state() as (root, state):
            entries = state["entries"]
            if any(
                item.get("locator_sha256") == locator.locator_sha256
                or item.get("payload_sha256") == locator.payload_sha256
                or item.get("attestation_id") == locator.attestation_id
                or item.get("nonce") == locator.nonce
                for item in entries
                if type(item) is dict
            ):
                _fail("WA_IR_REQUEST_PROVISIONING_REPLAY_REJECTED")
            workspace = _private_directory(
                facts.age_config.workspace_root,
                code="WA_IR_REQUEST_PROVISIONING_AGE_CONFIG_INVALID",
            )
            try:
                with tempfile.TemporaryDirectory(prefix="wa-ir-preflight-request-", dir=workspace) as temporary_text:
                    temporary = Path(temporary_text)
                    os.chmod(temporary, 0o700)
                    ciphertext = temporary / "payload.age"
                    plaintext = temporary / "payload.json"
                    descriptor = os.open(ciphertext, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    try:
                        reader = ArvanExactVersionPullReader(
                            config=facts.pull_config,
                            client_factory=lambda **_kwargs: self._new_client(facts, locator),
                            expectations=(
                                ArvanExactVersionPullExpectation(
                                    object_key=locator.object.object_key,
                                    version_id=locator.object.version_id,
                                    ciphertext_sha256=locator.object.ciphertext_sha256,
                                    ciphertext_bytes=locator.object.ciphertext_bytes,
                                    metadata=dict(locator.object.metadata),
                                ),
                            ),
                        )
                        reader.read_exact_to_fd(
                            object_key=locator.object.object_key,
                            version_id=locator.object.version_id,
                            destination_fd=descriptor,
                        )
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    decryptor = self._new_decryptor(facts)
                    decryptor.decrypt(
                        expected_recipient=locator.age_recipient,
                        ciphertext_path=ciphertext,
                        plaintext_path=plaintext,
                    )
                    payload_raw = _read_private_file(
                        plaintext,
                        exact_mode=0o600,
                        maximum=_protocol.MAX_FI_WA_IR_PREFLIGHT_REQUEST_PAYLOAD_BYTES,
                        code="WA_IR_REQUEST_PROVISIONING_PLAINTEXT_UNSAFE",
                    )
            except DedicatedHostPreflightIrRequestProvisioningRuntimeError:
                raise
            except Exception:
                _fail("WA_IR_REQUEST_PROVISIONING_PULL_OR_DECRYPT_FAILED")
            completed = self._now()
            if completed < started:
                _fail("WA_IR_REQUEST_PROVISIONING_CLOCK_INVALID")
            final_facts = _facts(self._config, now=completed, require_enabled=True)
            if final_facts != facts:
                _fail("WA_IR_REQUEST_PROVISIONING_POLICY_CHANGED")
            try:
                payload = _protocol.verify_fi_wa_ir_preflight_request_payload(
                    canonical_payload=payload_raw,
                    expected_fi_public_key=facts.fi_public_key,
                    now=completed,
                )
            except Exception:
                _fail("WA_IR_REQUEST_PROVISIONING_PAYLOAD_REJECTED")
            self._verify_payload_binding(payload, locator, final_facts)
            _install_request(payload.request.canonical_request)
            state["entries"].append(
                {
                    "locator_sha256": locator.locator_sha256,
                    "payload_sha256": payload.payload_sha256,
                    "request_sha256": payload.request.attestation_request_sha256,
                    "attestation_id": payload.request.attestation_id,
                    "nonce": payload.request.nonce,
                }
            )
            _write_state(root, state)
        return WaIrPreflightRequestInstallation(
            locator_sha256=locator.locator_sha256,
            payload_sha256=payload.payload_sha256,
            request_sha256=payload.request.attestation_request_sha256,
            attestation_id=payload.request.attestation_id,
            nonce=payload.request.nonce,
        )
