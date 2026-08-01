"""Root-only FI publisher for the signed WA-IR preflight-request route.

The runtime has two deliberately separate calls: ``sign_request`` accepts the
already typed WA-IR request plus a fresh paired Object-Storage preflight; and
``publish_signed_request`` accepts only the resulting canonical FI-signed
payload.  It encrypts that payload for one configured WA-IR age recipient,
uses only the FI publisher machine user to create one immutable object and
read back that exact version, then writes a redacted version-pinned locator
locally on FI.  It never opens the IR credential or identity and has no
direct FI-to-IR transport.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import importlib
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Protocol

from cryptography.hazmat.primitives import serialization

from core import dedicated_host_preflight_ir_request_provisioning as _protocol
from core import physical_arvan_immutability_preflight as _preflight
from core import physical_arvan_s3_fi_publisher_role_factory as _fi_publisher_role
from core import physical_arvan_s3_role_local_credential_reader as _credential_reader
from core import physical_age_v1_adapter as _age
from core.dedicated_host_preflight_ir_witness_attestation import ParsedWaIrWitnessAttestationRequest
from core.physical_arvan_exact_version_pull import (
    ArvanExactVersionPullExpectation,
    ArvanExactVersionPullReader,
    RootOwnedArvanExactVersionPullConfig,
    validate_arvan_exact_version_pull_config,
)
from core.physical_age_v1_adapter import PhysicalAgeV1Encryptor, PhysicalAgeV1EncryptorConfig
from core.physical_arvan_s3_role_local_route_policy import ArvanS3RoleLocalRoutePolicy
from core.physical_wal_object_manifest import PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE


__all__ = (
    "DEDICATED_HOST_PREFLIGHT_FI_REQUEST_PROVISIONING_DEFAULT_ENABLED",
    "FIXED_FI_WA_IR_REQUEST_PROVISIONING_KEY_FILE",
    "FIXED_FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_FILE",
    "DedicatedHostPreflightFiRequestProvisioningRuntimeError",
    "FiWaIrPreflightRequestPublication",
    "RootOwnedFiWaIrPreflightRequestProvisioningRuntime",
    "RootOwnedFiWaIrPreflightRequestProvisioningRuntimeConfig",
    "validate_root_owned_fi_wa_ir_preflight_request_provisioning_runtime_config",
)


DEDICATED_HOST_PREFLIGHT_FI_REQUEST_PROVISIONING_DEFAULT_ENABLED = False

# Both paths are intentionally FI-only and do not overlap the WA-IR key,
# identity, credential, request, or locator paths.
FIXED_FI_WA_IR_REQUEST_PROVISIONING_KEY_FILE = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/fi-wa-ir-request-provisioning-key.json"
)
FIXED_FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_FILE = Path(
    "/var/lib/trading-bot/dedicated-host-preflight/fi-wa-ir-attestation-request-locator.json"
)

_SCHEMA = "three-site-dedicated-host-preflight-fi-wa-ir-request-provisioning-runtime-v1"
_MODE = "root-owned-fi-signed-age-v1-create-only-exact-version-v1"
_SOURCE_SITE = "webapp_fi"
_DESTINATION_SITE = "webapp_ir"
_MAX_PRIVATE_FILE_BYTES = 32 * 1024


class DedicatedHostPreflightFiRequestProvisioningRuntimeError(ValueError):
    """One redacted local failure; it never includes a credential or path."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedFiWaIrPreflightRequestProvisioningRuntimeConfig:
    """Default-off FI-only policy; all secret paths are fixed constants."""

    schema: str = _SCHEMA
    exact_pull_config: RootOwnedArvanExactVersionPullConfig | None = field(
        default=None, repr=False, compare=False
    )
    age_encryptor_config: PhysicalAgeV1EncryptorConfig | None = field(
        default=None, repr=False, compare=False
    )
    preflight: _preflight.VerifiedPhysicalArvanImmutabilityPreflight | None = field(
        default=None, repr=False, compare=False
    )
    expected_fi_request_signer_public_key: bytes = b""
    enabled: bool = DEDICATED_HOST_PREFLIGHT_FI_REQUEST_PROVISIONING_DEFAULT_ENABLED
    source_site: str = _SOURCE_SITE
    destination_site: str = _DESTINATION_SITE
    mode: str = _MODE
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class FiWaIrPreflightRequestPublication:
    """Non-secret result; locator is explicitly redacted and version-pinned."""

    canonical_locator: bytes
    locator_sha256: str
    payload_sha256: str
    request_sha256: str
    object_key: str
    version_id: str


@dataclass(frozen=True)
class _Facts:
    pull_config: RootOwnedArvanExactVersionPullConfig
    age_config: PhysicalAgeV1EncryptorConfig
    preflight: _preflight.VerifiedPhysicalArvanImmutabilityPreflight
    fi_public_key: bytes
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
AgeEncryptorFactory = Callable[[PhysicalAgeV1EncryptorConfig], object]


def _fail(code: str) -> None:
    raise DedicatedHostPreflightFiRequestProvisioningRuntimeError(code)


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_ROOT_REQUIRED")
    except OSError:
        _fail("FI_WA_IR_REQUEST_PROVISIONING_ROOT_REQUIRED")


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


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


def _facts(value: object, *, now: datetime, require_enabled: bool) -> _Facts:
    if type(value) is not RootOwnedFiWaIrPreflightRequestProvisioningRuntimeConfig:
        _fail("FI_WA_IR_REQUEST_PROVISIONING_CONFIG_INVALID")
    if (
        value.schema != _SCHEMA
        or type(value.enabled) is not bool
        or value.source_site != _SOURCE_SITE
        or value.destination_site != _DESTINATION_SITE
        or value.mode != _MODE
        or value.direct_site_control != "forbidden"
        or value.destination_object_ingest != "pull-only"
        or type(value.exact_pull_config) is not RootOwnedArvanExactVersionPullConfig
        or type(value.age_encryptor_config) is not PhysicalAgeV1EncryptorConfig
        or type(value.preflight) is not _preflight.VerifiedPhysicalArvanImmutabilityPreflight
    ):
        _fail("FI_WA_IR_REQUEST_PROVISIONING_CONFIG_INVALID")
    if require_enabled and value.enabled is not True:
        _fail("FI_WA_IR_REQUEST_PROVISIONING_DISABLED")
    try:
        pull_config = validate_arvan_exact_version_pull_config(value.exact_pull_config)
    except Exception:
        _fail("FI_WA_IR_REQUEST_PROVISIONING_CONFIG_INVALID")
    if require_enabled and pull_config.enabled is not True:
        _fail("FI_WA_IR_REQUEST_PROVISIONING_DISABLED")
    age_config = value.age_encryptor_config
    if (
        age_config.enabled is not True
        or age_config.direct_site_control != "forbidden"
        or age_config.destination_object_ingest != "pull-only"
        or type(age_config.recipient) is not str
        or not age_config.recipient
        or type(age_config.maximum_plaintext_bytes) is not int
        or type(age_config.maximum_ciphertext_bytes) is not int
        or not 1 <= age_config.maximum_plaintext_bytes <= _protocol.MAX_FI_WA_IR_PREFLIGHT_REQUEST_PAYLOAD_BYTES
        or not age_config.maximum_plaintext_bytes <= age_config.maximum_ciphertext_bytes <= _protocol.MAX_FI_WA_IR_PREFLIGHT_REQUEST_CIPHERTEXT_BYTES
    ):
        _fail("FI_WA_IR_REQUEST_PROVISIONING_AGE_CONFIG_INVALID")
    _private_directory(age_config.workspace_root, code="FI_WA_IR_REQUEST_PROVISIONING_AGE_CONFIG_INVALID")
    if pull_config.maximum_ciphertext_bytes < age_config.maximum_ciphertext_bytes:
        _fail("FI_WA_IR_REQUEST_PROVISIONING_AGE_CONFIG_INVALID")
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
        _fail("FI_WA_IR_REQUEST_PROVISIONING_PREFLIGHT_INVALID")
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
        _fail("FI_WA_IR_REQUEST_PROVISIONING_PREFLIGHT_ROUTE_INVALID")
    return _Facts(
        pull_config=pull_config,
        age_config=age_config,
        preflight=preflight,
        fi_public_key=_public_key(
            value.expected_fi_request_signer_public_key,
            code="FI_WA_IR_REQUEST_PROVISIONING_SIGNER_PIN_INVALID",
        ),
        fi_identity_sha256=fi_identity,
        ir_identity_sha256=ir_identity,
    )


def validate_root_owned_fi_wa_ir_preflight_request_provisioning_runtime_config(
    config: RootOwnedFiWaIrPreflightRequestProvisioningRuntimeConfig,
) -> RootOwnedFiWaIrPreflightRequestProvisioningRuntimeConfig:
    """Inert structural validation; it opens no file, SDK, age binary, or socket."""

    if type(config) is not RootOwnedFiWaIrPreflightRequestProvisioningRuntimeConfig:
        _fail("FI_WA_IR_REQUEST_PROVISIONING_CONFIG_INVALID")
    if (
        config.schema != _SCHEMA
        or type(config.enabled) is not bool
        or config.source_site != _SOURCE_SITE
        or config.destination_site != _DESTINATION_SITE
        or config.mode != _MODE
        or config.direct_site_control != "forbidden"
        or config.destination_object_ingest != "pull-only"
        or type(config.exact_pull_config) is not RootOwnedArvanExactVersionPullConfig
        or type(config.age_encryptor_config) is not PhysicalAgeV1EncryptorConfig
        or type(config.preflight) is not _preflight.VerifiedPhysicalArvanImmutabilityPreflight
    ):
        _fail("FI_WA_IR_REQUEST_PROVISIONING_CONFIG_INVALID")
    try:
        validate_arvan_exact_version_pull_config(config.exact_pull_config)
    except Exception:
        _fail("FI_WA_IR_REQUEST_PROVISIONING_CONFIG_INVALID")
    _public_key(config.expected_fi_request_signer_public_key, code="FI_WA_IR_REQUEST_PROVISIONING_SIGNER_PIN_INVALID")
    return config


def _read_root_file(path: Path, *, exact_mode: int, maximum: int, code: str) -> bytes:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts or not hasattr(os, "O_NOFOLLOW"):
        _fail(code)
    _private_directory(path.parent, code=code)
    descriptor = -1
    try:
        before = os.lstat(path)
        resolved = path.resolve(strict=True)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(descriptor)
        if (
            resolved != path
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != exact_mode
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or not 1 <= opened.st_size <= maximum
        ):
            _fail(code)
        content = bytearray()
        while len(content) < opened.st_size:
            chunk = os.read(descriptor, opened.st_size - len(content))
            if not chunk:
                _fail(code)
            content.extend(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        return bytes(content)
    except DedicatedHostPreflightFiRequestProvisioningRuntimeError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_atomic_root_file(path: Path, payload: bytes, *, code: str) -> None:
    if type(payload) is not bytes or not payload or len(payload) > _MAX_PRIVATE_FILE_BYTES:
        _fail(code)
    directory = _private_directory(path.parent, code=code)
    if not hasattr(os, "O_NOFOLLOW"):
        _fail(code)
    descriptor = -1
    temporary = directory / ("." + path.name + ".new")
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
                _fail(code)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except DedicatedHostPreflightFiRequestProvisioningRuntimeError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if created:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _role_local_route_policy(facts: _Facts) -> ArvanS3RoleLocalRoutePolicy:
    """Construct the sole non-secret normal route accepted by FI artifact."""

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
    return _fi_publisher_role.load_root_owned_arvan_s3_fi_publisher_role_credential_facts(
        route_policy
    )


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
        _fail("FI_WA_IR_REQUEST_PROVISIONING_SDK_CLIENT_FAILED")
    if client is None:
        _fail("FI_WA_IR_REQUEST_PROVISIONING_SDK_CLIENT_FAILED")
    return client


class _FiExactObjectClient:
    """Private S3 surface: one create-only PUT and one exact GET only."""

    def __init__(self, *, raw: object, bucket: str, object_key: str) -> None:
        self._raw = raw
        self._bucket = bucket
        self._object_key = object_key

    def put_object(self, **request: Any) -> Mapping[str, Any]:
        value = dict(request)
        expected = {"Bucket", "Key", "Body", "ContentLength", "Metadata", "ContentType", "IfNoneMatch"}
        if (
            set(value) != expected
            or value.get("Bucket") != self._bucket
            or value.get("Key") != self._object_key
            or value.get("IfNoneMatch") != "*"
            or value.get("ContentType") != "application/octet-stream"
            or type(value.get("ContentLength")) is not int
            or type(value.get("Body")) is not bytes
            or value["ContentLength"] != len(value["Body"])
            or not isinstance(value.get("Metadata"), Mapping)
        ):
            _fail("FI_WA_IR_REQUEST_PROVISIONING_PUT_INVALID")
        try:
            method = getattr(self._raw, "put_object")
            response = method(**value)
        except Exception:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_PUT_FAILED")
        if not isinstance(response, Mapping):
            _fail("FI_WA_IR_REQUEST_PROVISIONING_PUT_FAILED")
        return dict(response)

    def get_object(self, *, Bucket: str, Key: str, VersionId: str) -> Mapping[str, Any]:
        if Bucket != self._bucket or Key != self._object_key or type(VersionId) is not str or not VersionId:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_EXACT_GET_INVALID")
        try:
            method = getattr(self._raw, "get_object")
            response = method(Bucket=Bucket, Key=Key, VersionId=VersionId)
        except Exception:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_EXACT_GET_FAILED")
        if not isinstance(response, Mapping):
            _fail("FI_WA_IR_REQUEST_PROVISIONING_EXACT_GET_FAILED")
        return dict(response)


def _object_key(payload: _protocol.VerifiedFiWaIrPreflightRequestPayload) -> str:
    request = payload.request
    return (
        "dedicated-host-preflight/v1/"
        + request.readonly_request["campaign_id"]
        + "/"
        + request.readonly_request["operation_id"]
        + "/wa-ir-witness-request/"
        + request.attestation_id
        + "-"
        + payload.payload_sha256
        + ".age"
    )


def _read_private_bytes(path: Path, *, maximum: int, code: str) -> bytes:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail(code)
    descriptor = -1
    try:
        before = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        metadata = os.fstat(descriptor)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_dev != before.st_dev
            or metadata.st_ino != before.st_ino
            or not 1 <= metadata.st_size <= maximum
        ):
            _fail(code)
        value = bytearray()
        while len(value) < metadata.st_size:
            chunk = os.read(descriptor, metadata.st_size - len(value))
            if not chunk:
                _fail(code)
            value.extend(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        return bytes(value)
    except DedicatedHostPreflightFiRequestProvisioningRuntimeError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


class RootOwnedFiWaIrPreflightRequestProvisioningRuntime:
    """FI-only signing and immutable publication; construction is inert."""

    def __init__(
        self,
        config: RootOwnedFiWaIrPreflightRequestProvisioningRuntimeConfig,
        *,
        clock: Callable[[], datetime] | None,
        credential_admitter: CredentialAdmitter = _default_credential_admitter,
        raw_s3_client_builder: _RawS3ClientBuilder = _default_raw_s3_client_builder,
        age_encryptor_factory: AgeEncryptorFactory | None = None,
    ) -> None:
        self._config = validate_root_owned_fi_wa_ir_preflight_request_provisioning_runtime_config(config)
        self._clock = clock
        self._credential_admitter = credential_admitter
        self._raw_s3_client_builder = raw_s3_client_builder
        self._age_encryptor_factory = age_encryptor_factory

    def _now(self) -> datetime:
        if self._clock is None or not callable(self._clock):
            _fail("FI_WA_IR_REQUEST_PROVISIONING_CLOCK_REQUIRED")
        try:
            return _utc(self._clock(), code="FI_WA_IR_REQUEST_PROVISIONING_CLOCK_INVALID")
        except DedicatedHostPreflightFiRequestProvisioningRuntimeError:
            raise
        except Exception:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_CLOCK_INVALID")

    def _signer(self, facts: _Facts):
        raw = _read_root_file(
            FIXED_FI_WA_IR_REQUEST_PROVISIONING_KEY_FILE,
            exact_mode=0o400,
            maximum=_protocol.MAX_FI_WA_IR_PREFLIGHT_REQUEST_KEY_BYTES,
            code="FI_WA_IR_REQUEST_PROVISIONING_KEY_FILE_UNSAFE",
        )
        try:
            signer = _protocol.parse_fi_wa_ir_preflight_request_provisioning_key_record(raw)
            public = signer.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        except Exception:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_KEY_FILE_INVALID")
        if public != facts.fi_public_key:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_KEY_PIN_MISMATCH")
        return signer

    @staticmethod
    def _payload_binding(
        request: ParsedWaIrWitnessAttestationRequest,
        facts: _Facts,
        now: datetime,
    ) -> _protocol.FiWaIrPreflightRequestProvisioningBinding:
        if (
            facts.preflight.binding.campaign_id != request.readonly_request["campaign_id"]
            or facts.preflight.binding.release_sha != request.readonly_request["release_sha"]
        ):
            _fail("FI_WA_IR_REQUEST_PROVISIONING_PREFLIGHT_CAMPAIGN_MISMATCH")
        return _protocol.FiWaIrPreflightRequestProvisioningBinding(
            route_binding_sha256=facts.preflight.binding.route_binding_sha256,
            fi_publisher_identity_sha256=facts.fi_identity_sha256,
            ir_receiver_identity_sha256=facts.ir_identity_sha256,
            age_recipient=facts.age_config.recipient,
            issued_at=now,
            maximum_validity_seconds=request.maximum_validity_seconds,
        )

    def sign_request(self, *, request: ParsedWaIrWitnessAttestationRequest) -> bytes:
        """Accept one typed request and fresh provider binding; sign no raw JSON."""

        _require_root()
        now = self._now()
        facts = _facts(self._config, now=now, require_enabled=True)
        try:
            signer = self._signer(facts)
            payload = _protocol.build_fi_wa_ir_preflight_request_payload(
                request=request,
                binding=self._payload_binding(request, facts, now),
                signer=signer,
            )
            _protocol.verify_fi_wa_ir_preflight_request_payload(
                canonical_payload=payload,
                expected_fi_public_key=facts.fi_public_key,
                now=now,
            )
            return payload
        except DedicatedHostPreflightFiRequestProvisioningRuntimeError:
            raise
        except Exception:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_SIGN_FAILED")

    def _new_encryptor(self, facts: _Facts) -> object:
        try:
            result = (
                PhysicalAgeV1Encryptor(facts.age_config)
                if self._age_encryptor_factory is None
                else self._age_encryptor_factory(facts.age_config)
            )
        except Exception:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_AGE_ENCRYPTOR_INVALID")
        if not callable(getattr(result, "encrypt", None)):
            _fail("FI_WA_IR_REQUEST_PROVISIONING_AGE_ENCRYPTOR_INVALID")
        return result

    def _new_fi_client(self, facts: _Facts, *, object_key: str) -> _FiExactObjectClient:
        if not callable(self._credential_admitter) or not callable(self._raw_s3_client_builder):
            _fail("FI_WA_IR_REQUEST_PROVISIONING_CLIENT_FACTORY_INVALID")
        try:
            route, credential = self._credential_admitter(_role_local_route_policy(facts))
        except Exception:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_FI_CREDENTIAL_ADMISSION_FAILED")
        if (
            type(route) is not _credential_reader.ArvanS3RoleLocalRouteFacts
            or type(credential) is not _credential_reader.ArvanS3RoleLocalCredentialFacts
            or route.endpoint != facts.pull_config.endpoint
            or route.region != facts.pull_config.region
            or route.bucket != facts.pull_config.bucket
            or credential.identity_sha256 != facts.fi_identity_sha256
            or credential.identity_sha256 == facts.ir_identity_sha256
        ):
            _fail("FI_WA_IR_REQUEST_PROVISIONING_FI_CREDENTIAL_MISMATCH")
        try:
            raw = self._raw_s3_client_builder(
                endpoint=route.endpoint,
                region=route.region,
                access_key=credential.access_key,
                secret_key=credential.secret_key,
            )
        except Exception:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_SDK_CLIENT_FAILED")
        if raw is None:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_SDK_CLIENT_FAILED")
        return _FiExactObjectClient(raw=raw, bucket=route.bucket, object_key=object_key)

    def publish_signed_request(self, *, canonical_payload: bytes) -> FiWaIrPreflightRequestPublication:
        """Encrypt/create/read back one signed payload; emit no network locator relay."""

        _require_root()
        started = self._now()
        facts = _facts(self._config, now=started, require_enabled=True)
        try:
            payload = _protocol.verify_fi_wa_ir_preflight_request_payload(
                canonical_payload=canonical_payload,
                expected_fi_public_key=facts.fi_public_key,
                now=started,
            )
        except Exception:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_PAYLOAD_REJECTED")
        request = payload.request
        expected_binding = self._payload_binding(request, facts, started)
        if (
            payload.route_binding_sha256 != expected_binding.route_binding_sha256
            or payload.fi_publisher_identity_sha256 != expected_binding.fi_publisher_identity_sha256
            or payload.ir_receiver_identity_sha256 != expected_binding.ir_receiver_identity_sha256
            or payload.age_recipient != expected_binding.age_recipient
        ):
            _fail("FI_WA_IR_REQUEST_PROVISIONING_PAYLOAD_BINDING_MISMATCH")
        workspace = _private_directory(facts.age_config.workspace_root, code="FI_WA_IR_REQUEST_PROVISIONING_AGE_CONFIG_INVALID")
        object_key = _object_key(payload)
        try:
            with tempfile.TemporaryDirectory(prefix="fi-wa-ir-preflight-request-", dir=workspace) as temporary_text:
                temporary = Path(temporary_text)
                os.chmod(temporary, 0o700)
                plaintext = temporary / "payload.json"
                ciphertext = temporary / "payload.age"
                plaintext.write_bytes(canonical_payload)
                plaintext.chmod(0o600)
                encryptor = self._new_encryptor(facts)
                encryptor.encrypt(
                    recipient=facts.age_config.recipient,
                    plaintext_path=plaintext,
                    ciphertext_path=ciphertext,
                )
                encrypted = _read_private_bytes(
                    ciphertext,
                    maximum=_protocol.MAX_FI_WA_IR_PREFLIGHT_REQUEST_CIPHERTEXT_BYTES,
                    code="FI_WA_IR_REQUEST_PROVISIONING_CIPHERTEXT_UNSAFE",
                )
                ciphertext_sha256 = hashlib.sha256(encrypted).hexdigest()
                metadata = {
                    "encryption": "age-v1",
                    "ciphertext-sha256": ciphertext_sha256,
                    "ciphertext-bytes": str(len(encrypted)),
                    "payload-sha256": payload.payload_sha256,
                    "request-sha256": request.attestation_request_sha256,
                }
                client = self._new_fi_client(facts, object_key=object_key)
                put = client.put_object(
                    Bucket=facts.pull_config.bucket,
                    Key=object_key,
                    Body=encrypted,
                    ContentLength=len(encrypted),
                    Metadata=metadata,
                    ContentType="application/octet-stream",
                    IfNoneMatch="*",
                )
                version_id = put.get("VersionId")
                if type(version_id) is not str or not version_id or version_id.lower() in {"latest", "null", "undefined"}:
                    _fail("FI_WA_IR_REQUEST_PROVISIONING_PUT_VERSION_INVALID")
                expectation = ArvanExactVersionPullExpectation(
                    object_key=object_key,
                    version_id=version_id,
                    ciphertext_sha256=ciphertext_sha256,
                    ciphertext_bytes=len(encrypted),
                    metadata=metadata,
                )
                reader = ArvanExactVersionPullReader(
                    config=facts.pull_config,
                    client_factory=lambda **_kwargs: client,
                    expectations=(expectation,),
                )
                readback = temporary / "readback.age"
                fd = os.open(readback, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    receipt = reader.read_exact_to_fd(
                        object_key=object_key,
                        version_id=version_id,
                        destination_fd=fd,
                    )
                    os.fsync(fd)
                finally:
                    os.close(fd)
                if receipt.ciphertext_sha256 != ciphertext_sha256 or receipt.ciphertext_bytes != len(encrypted):
                    _fail("FI_WA_IR_REQUEST_PROVISIONING_READBACK_MISMATCH")
                signer = self._signer(facts)
                locator = _protocol.build_fi_wa_ir_preflight_request_locator(
                    canonical_payload=canonical_payload,
                    expected_fi_public_key=facts.fi_public_key,
                    object=_protocol.FiWaIrPreflightRequestLocator(
                        object_key=object_key,
                        version_id=version_id,
                        ciphertext_sha256=ciphertext_sha256,
                        ciphertext_bytes=len(encrypted),
                        metadata=metadata,
                    ),
                    signer=signer,
                    now=started,
                )
        except DedicatedHostPreflightFiRequestProvisioningRuntimeError:
            raise
        except Exception:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_PUBLISH_FAILED")
        completed = self._now()
        if completed < started:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_CLOCK_INVALID")
        final_facts = _facts(self._config, now=completed, require_enabled=True)
        if final_facts != facts:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_POLICY_CHANGED")
        try:
            verified_locator = _protocol.verify_fi_wa_ir_preflight_request_locator(
                canonical_locator=locator,
                expected_fi_public_key=facts.fi_public_key,
                now=completed,
            )
        except Exception:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_REJECTED")
        _write_atomic_root_file(
            FIXED_FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_FILE,
            locator,
            code="FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_WRITE_FAILED",
        )
        return FiWaIrPreflightRequestPublication(
            canonical_locator=locator,
            locator_sha256=verified_locator.locator_sha256,
            payload_sha256=payload.payload_sha256,
            request_sha256=request.attestation_request_sha256,
            object_key=verified_locator.object.object_key,
            version_id=verified_locator.object.version_id,
        )
