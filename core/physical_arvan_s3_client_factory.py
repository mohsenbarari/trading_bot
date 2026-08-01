"""Root-owned, fail-closed S3 client factories for the physical data plane.

This module is the deliberately narrow credential-to-client seam for the
already fail-closed physical Arvan adapters.  It has no import-time S3 SDK
dependency, file read, environment fallback, network action, bucket probe, or
Object operation.  A root-owned bootstrap must explicitly instantiate it with
one canonical endpoint/region/bucket policy and then inject one of its scoped
factories into a physical publisher or exact-version reader.

The only credential location is the fixed root-owned file constant below.  It
is not configurable through a dataclass, environment variable, URL, CLI, or
factory call.  Credentials never appear in a public return value, exception,
configuration, repr, or log message.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import importlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any


__all__ = (
    "ARVAN_S3_CLIENT_FACTORY_DEFAULT_ENABLED",
    "ARVAN_S3_CLIENT_FACTORY_SCHEMA",
    "FIXED_ARVAN_S3_CREDENTIAL_FILE",
    "ArvanS3ExactPullClientFactory",
    "ArvanS3PhysicalPublishClientFactory",
    "RootOwnedArvanS3ClientFactory",
    "RootOwnedArvanS3ClientFactoryConfig",
    "ArvanS3ClientFactoryError",
    "validate_root_owned_arvan_s3_client_factory_config",
)


ARVAN_S3_CLIENT_FACTORY_SCHEMA = "gold-trade-physical-arvan-s3-client-factory-v1"
ARVAN_S3_CLIENT_FACTORY_DEFAULT_ENABLED = False

# This is intentionally the *only* credential-file location.  Deployment may
# install the file there but cannot redirect this adapter to an arbitrary
# pathname.  The module does not create or open it until a factory is called.
FIXED_ARVAN_S3_CREDENTIAL_FILE = Path(
    "/etc/trading-bot/security/arvan-s3-credentials.json"
)

ARVAN_S3_CONNECT_TIMEOUT_SECONDS = 5
ARVAN_S3_READ_TIMEOUT_SECONDS = 60
ARVAN_S3_MAX_ATTEMPTS = 2
ARVAN_S3_MAX_CREDENTIAL_BYTES = 16 * 1024

_ENDPOINT_RE = re.compile(
    r"^https://s3\.([a-z0-9][a-z0-9-]{0,62})\.arvanstorage\.ir/?$",
    re.ASCII,
)
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
_SENSITIVE_OR_URL_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)
_CREDENTIAL_VALUE_RE = re.compile(r"^[\x21-\x7e]{1,1024}$", re.ASCII)


class ArvanS3ClientFactoryError(ValueError):
    """A secure physical S3 client cannot be constructed.

    Codes are intentionally fixed: caller selectors, endpoint text, file
    paths, credential material, and SDK exception text never cross this
    boundary.
    """

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedArvanS3ClientFactoryConfig:
    """Non-secret, one-bucket policy for physical Object Storage clients."""

    schema: str = ARVAN_S3_CLIENT_FACTORY_SCHEMA
    endpoint: str = ""
    region: str = ""
    bucket: str = ""
    enabled: bool = ARVAN_S3_CLIENT_FACTORY_DEFAULT_ENABLED
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class _FactoryFacts:
    endpoint: str
    region: str
    bucket: str


@dataclass(frozen=True)
class _Credentials:
    # This is private implementation state.  In particular, it is not part of
    # a public factory/configuration result and it cannot accidentally appear
    # in a dataclass repr or equality failure.
    access_key: str = field(repr=False, compare=False)
    secret_key: str = field(repr=False, compare=False)


def _fail(code: str) -> None:
    raise ArvanS3ClientFactoryError(code)


def _safe_bucket(value: object) -> str:
    if type(value) is not str or _BUCKET_RE.fullmatch(value) is None:
        _fail("ARVAN_S3_FACTORY_BUCKET_INVALID")
    if _SENSITIVE_OR_URL_RE.search(value) is not None:
        _fail("ARVAN_S3_FACTORY_BUCKET_INVALID")
    return value


def validate_root_owned_arvan_s3_client_factory_config(
    config: RootOwnedArvanS3ClientFactoryConfig,
) -> RootOwnedArvanS3ClientFactoryConfig:
    """Purely validate one non-secret canonical Object Storage policy."""

    if type(config) is not RootOwnedArvanS3ClientFactoryConfig:
        _fail("ARVAN_S3_FACTORY_CONFIG_TYPE_INVALID")
    if config.schema != ARVAN_S3_CLIENT_FACTORY_SCHEMA:
        _fail("ARVAN_S3_FACTORY_SCHEMA_INVALID")
    if type(config.endpoint) is not str:
        _fail("ARVAN_S3_FACTORY_ENDPOINT_INVALID")
    match = _ENDPOINT_RE.fullmatch(config.endpoint)
    if match is None:
        _fail("ARVAN_S3_FACTORY_ENDPOINT_INVALID")
    endpoint_region = match.group(1)
    if type(config.region) is not str or config.region != endpoint_region:
        _fail("ARVAN_S3_FACTORY_REGION_INVALID")
    if type(config.enabled) is not bool:
        _fail("ARVAN_S3_FACTORY_ENABLED_INVALID")
    if config.direct_site_control != "forbidden" or config.destination_object_ingest != "pull-only":
        _fail("ARVAN_S3_FACTORY_DIRECTION_POLICY_INVALID")
    return RootOwnedArvanS3ClientFactoryConfig(
        schema=ARVAN_S3_CLIENT_FACTORY_SCHEMA,
        endpoint=f"https://s3.{endpoint_region}.arvanstorage.ir",
        region=endpoint_region,
        bucket=_safe_bucket(config.bucket),
        enabled=config.enabled,
        direct_site_control="forbidden",
        destination_object_ingest="pull-only",
    )


def _enabled_facts(config: RootOwnedArvanS3ClientFactoryConfig) -> _FactoryFacts:
    validated = validate_root_owned_arvan_s3_client_factory_config(config)
    if validated.enabled is not True:
        _fail("ARVAN_S3_FACTORY_DISABLED")
    return _FactoryFacts(
        endpoint=validated.endpoint,
        region=validated.region,
        bucket=validated.bucket,
    )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("ARVAN_S3_FACTORY_CREDENTIAL_FILE_INVALID")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    del value
    _fail("ARVAN_S3_FACTORY_CREDENTIAL_FILE_INVALID")


def _fixed_private_credential_file() -> Path:
    path = FIXED_ARVAN_S3_CREDENTIAL_FILE
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail("ARVAN_S3_FACTORY_CREDENTIAL_FILE_INVALID")
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
        parent = path.parent
        parent_metadata = os.lstat(parent)
        parent_resolved = parent.resolve(strict=True)
    except OSError:
        _fail("ARVAN_S3_FACTORY_CREDENTIAL_FILE_INVALID")
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
        or stat.S_IMODE(parent_metadata.st_mode) & 0o022
        or metadata.st_size < 2
        or metadata.st_size > ARVAN_S3_MAX_CREDENTIAL_BYTES
        or not hasattr(os, "O_NOFOLLOW")
    ):
        _fail("ARVAN_S3_FACTORY_CREDENTIAL_FILE_INVALID")
    return resolved


def _credential_file_bytes(path: Path) -> bytes:
    try:
        before = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        _fail("ARVAN_S3_FACTORY_CREDENTIAL_FILE_INVALID")
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size < 2
            or metadata.st_size > ARVAN_S3_MAX_CREDENTIAL_BYTES
            or before.st_dev != metadata.st_dev
            or before.st_ino != metadata.st_ino
        ):
            _fail("ARVAN_S3_FACTORY_CREDENTIAL_FILE_INVALID")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 4096)
            if type(chunk) is not bytes:
                _fail("ARVAN_S3_FACTORY_CREDENTIAL_FILE_INVALID")
            if not chunk:
                break
            total += len(chunk)
            if total > ARVAN_S3_MAX_CREDENTIAL_BYTES:
                _fail("ARVAN_S3_FACTORY_CREDENTIAL_FILE_INVALID")
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
            _fail("ARVAN_S3_FACTORY_CREDENTIAL_FILE_INVALID")
        return b"".join(chunks)
    except OSError:
        _fail("ARVAN_S3_FACTORY_CREDENTIAL_FILE_INVALID")
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _credential_value(value: object) -> str:
    if type(value) is not str or _CREDENTIAL_VALUE_RE.fullmatch(value) is None:
        _fail("ARVAN_S3_FACTORY_CREDENTIAL_FILE_INVALID")
    return value


def _load_fixed_credentials() -> _Credentials:
    raw = _credential_file_bytes(_fixed_private_credential_file())
    try:
        decoded = raw.decode("utf-8", "strict")
        value = json.loads(
            decoded,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("ARVAN_S3_FACTORY_CREDENTIAL_FILE_INVALID")
    if type(value) is not dict or set(value) != {"access_key", "secret_key"}:
        _fail("ARVAN_S3_FACTORY_CREDENTIAL_FILE_INVALID")
    return _Credentials(
        access_key=_credential_value(value["access_key"]),
        secret_key=_credential_value(value["secret_key"]),
    )


def _load_boto_sdk() -> tuple[object, object]:
    """Import SDK modules only after the credential file passed validation."""

    try:
        boto3_module = importlib.import_module("boto3")
        botocore_config_module = importlib.import_module("botocore.config")
    except Exception:
        _fail("ARVAN_S3_FACTORY_SDK_UNAVAILABLE")
    return boto3_module, botocore_config_module


class _BucketScopedArvanS3Client:
    """Expose only the physical contracts' bucket-bound S3 operation surface.

    It does not invoke an S3 operation during creation.  Each explicit caller
    operation is checked for the configured bucket before it can reach the SDK
    client; no arbitrary `__getattr__` escape hatch is provided.
    """

    def __init__(self, *, client: object, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def _call(self, method_name: str, request: Mapping[str, Any]) -> Any:
        if type(request) is not dict or type(request.get("Bucket")) is not str:
            _fail("ARVAN_S3_FACTORY_BUCKET_REQUEST_INVALID")
        if request["Bucket"] != self._bucket:
            _fail("ARVAN_S3_FACTORY_BUCKET_MISMATCH")
        method = getattr(self._client, method_name, None)
        if not callable(method):
            _fail("ARVAN_S3_FACTORY_CLIENT_INVALID")
        try:
            return method(**dict(request))
        except Exception:
            _fail("ARVAN_S3_FACTORY_CLIENT_OPERATION_FAILED")

    def get_bucket_versioning(self, *, Bucket: str) -> Any:
        return self._call("get_bucket_versioning", {"Bucket": Bucket})

    def get_bucket_acl(self, *, Bucket: str) -> Any:
        return self._call("get_bucket_acl", {"Bucket": Bucket})

    def list_object_versions(self, **request: Any) -> Any:
        return self._call("list_object_versions", dict(request))

    def put_object(self, **request: Any) -> Any:
        return self._call("put_object", dict(request))

    def head_object(self, **request: Any) -> Any:
        return self._call("head_object", dict(request))

    def get_object(self, **request: Any) -> Any:
        return self._call("get_object", dict(request))


class RootOwnedArvanS3ClientFactory:
    """One root-owned policy that yields narrow publish and exact-pull factories."""

    def __init__(self, config: RootOwnedArvanS3ClientFactoryConfig) -> None:
        # Validation is deliberately pure.  Do not inspect the fixed file or
        # import boto here, so disabled/not-yet-provisioned hosts stay inert.
        self._config = validate_root_owned_arvan_s3_client_factory_config(config)

    def exact_pull_client_factory(self) -> "ArvanS3ExactPullClientFactory":
        """Return the keyword-only factory expected by exact-version readers."""

        return ArvanS3ExactPullClientFactory(self)

    def physical_publish_client_factory(self) -> "ArvanS3PhysicalPublishClientFactory":
        """Return the zero-argument factory expected by physical publishers."""

        return ArvanS3PhysicalPublishClientFactory(self)

    def _create_client(self, *, endpoint: str, region: str) -> _BucketScopedArvanS3Client:
        facts = _enabled_facts(self._config)
        if type(endpoint) is not str or endpoint != facts.endpoint:
            _fail("ARVAN_S3_FACTORY_ENDPOINT_MISMATCH")
        if type(region) is not str or region != facts.region:
            _fail("ARVAN_S3_FACTORY_REGION_MISMATCH")
        # Keep this ordering: secure policy and credential validation finish
        # before importing boto3/botocore, constructing a session, or allowing
        # any SDK code to look for its own credentials.
        credentials = _load_fixed_credentials()
        boto3_module, botocore_config_module = _load_boto_sdk()
        try:
            session_namespace = getattr(boto3_module, "session")
            session_type = getattr(session_namespace, "Session")
            config_type = getattr(botocore_config_module, "Config")
            if not callable(session_type) or not callable(config_type):
                _fail("ARVAN_S3_FACTORY_SDK_UNAVAILABLE")
            botocore_config = config_type(
                signature_version="s3v4",
                connect_timeout=ARVAN_S3_CONNECT_TIMEOUT_SECONDS,
                read_timeout=ARVAN_S3_READ_TIMEOUT_SECONDS,
                retries={"max_attempts": ARVAN_S3_MAX_ATTEMPTS, "mode": "standard"},
                s3={"addressing_style": "path"},
                # An explicit empty mapping prevents a caller/configuration
                # from supplying an arbitrary proxy endpoint.
                proxies={},
            )
            session = session_type(
                aws_access_key_id=credentials.access_key,
                aws_secret_access_key=credentials.secret_key,
                region_name=facts.region,
            )
            client = session.client(
                "s3",
                endpoint_url=facts.endpoint,
                region_name=facts.region,
                use_ssl=True,
                verify=True,
                config=botocore_config,
            )
        except ArvanS3ClientFactoryError:
            raise
        except Exception:
            _fail("ARVAN_S3_FACTORY_CLIENT_CREATE_FAILED")
        return _BucketScopedArvanS3Client(client=client, bucket=facts.bucket)


class ArvanS3ExactPullClientFactory:
    """Keyword-only, exact endpoint/region factory for `ArvanExactVersionPullReader`."""

    def __init__(self, owner: RootOwnedArvanS3ClientFactory) -> None:
        self._owner = owner

    def __call__(self, *, endpoint: str, region: str) -> _BucketScopedArvanS3Client:
        if type(self._owner) is not RootOwnedArvanS3ClientFactory:
            _fail("ARVAN_S3_FACTORY_OWNER_INVALID")
        return self._owner._create_client(endpoint=endpoint, region=region)


class ArvanS3PhysicalPublishClientFactory:
    """Zero-argument bucket-scoped factory for physical publisher contracts."""

    def __init__(self, owner: RootOwnedArvanS3ClientFactory) -> None:
        self._owner = owner

    def __call__(self) -> _BucketScopedArvanS3Client:
        if type(self._owner) is not RootOwnedArvanS3ClientFactory:
            _fail("ARVAN_S3_FACTORY_OWNER_INVALID")
        # There are no caller selectors on this path; endpoint/region are the
        # immutable root policy values and bucket is enforced by the wrapper.
        facts = _enabled_facts(self._owner._config)
        return self._owner._create_client(endpoint=facts.endpoint, region=facts.region)
