"""Root-only, read-only Arvan ECC provider evidence adapter.

This adapter implements only the controller's ``ProviderReadback`` seam.  It
constructs one fixed GET request for one source-pinned disposable host and
normalizes a verified Arvan ECC server inventory response into the controller's
canonical raw provider-evidence bytes.  It contains no HTTP client, SDK,
network call, shell, subprocess, provider mutation, or host mutation.

The sole future transport seam is an injected bounded runner.  The runner is
given an immutable invocation with the fixed ECC endpoint, the sole permitted
GET path, and an opaque API key loaded from one fixed root-owned file.  Neither
the key nor the upstream response body is returned, logged, or included in a
raised error.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Protocol
from uuid import UUID

from core.dedicated_host_preflight_controller import (
    DELIVERY_CONTRACT_BY_ROLE,
    PROVIDER_NAME,
    PROVIDER_READBACK_MODE,
    PROVIDER_READBACK_PATH_BY_ROLE,
    PROVIDER_READBACK_RESPONSE_SCHEMA,
    PROVIDER_READBACK_SCHEMA,
    DedicatedHostTarget,
)
from core.dedicated_host_preflight_receipt import HEX64, canonical_json_bytes
from scripts.dedicated_host_preflight_manifest import EXPECTED_HOSTS, ROLE_ORDER


__all__ = (
    "ARVAN_ECC_READBACK_CONFIG_SCHEMA",
    "ARVAN_ECC_READBACK_DEFAULT_ENABLED",
    "ARVAN_ECC_READBACK_SECRET_CONFIG_SCHEMA",
    "FIXED_ARVAN_ECC_ENDPOINT",
    "FIXED_ARVAN_ECC_READBACK_CONFIG_FILE",
    "ArvanEccGetServerInvocation",
    "ArvanEccGetServerRunner",
    "ArvanEccGetServerRunnerResult",
    "ArvanEccProviderReadbackError",
    "RootOwnedArvanEccProviderReadback",
    "RootOwnedArvanEccProviderReadbackConfig",
)


ARVAN_ECC_READBACK_CONFIG_SCHEMA = (
    "three-site-dedicated-host-preflight-arvan-ecc-readback-config-v1"
)
ARVAN_ECC_READBACK_SECRET_CONFIG_SCHEMA = (
    "three-site-dedicated-host-preflight-arvan-ecc-readback-secret-config-v1"
)
ARVAN_ECC_READBACK_DEFAULT_ENABLED = False

# This endpoint and this one secret file location are deliberately not
# constructor, environment, command-line, or runner inputs.
FIXED_ARVAN_ECC_ENDPOINT = "https://napi.arvancloud.ir/ecc/v1"
FIXED_ARVAN_ECC_READBACK_CONFIG_FILE = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/arvan-ecc-readback.json"
)

_FIXED_METHOD = "GET"
_FIXED_AUTH_SCHEME = "Apikey"
_MAX_SECRET_CONFIG_BYTES = 16 * 1024
_MAX_ECC_RESPONSE_BYTES = 64 * 1024
_MAX_JSON_DEPTH = 32
_MAX_JSON_MEMBERS = 2_048
_SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$", re.ASCII)
_API_KEY_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{16,4096}$", re.ASCII)
_URL_OR_SECRET_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)
_FORBIDDEN_RESPONSE_KEY_PARTS = frozenset(
    {
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "endpoint",
        "header",
        "password",
        "proxy",
        "secret",
        "token",
        "uri",
        "url",
    }
)


class ArvanEccProviderReadbackError(ValueError):
    """A fixed-code failure that never includes credential or response text."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedArvanEccProviderReadbackConfig:
    """Public non-secret switch; endpoint, file path, and targets are fixed."""

    schema: str = ARVAN_ECC_READBACK_CONFIG_SCHEMA
    enabled: bool = ARVAN_ECC_READBACK_DEFAULT_ENABLED


@dataclass(frozen=True)
class ArvanEccGetServerInvocation:
    """The only request a future bounded runner can receive.

    ``api_key`` is private implementation state.  It is redacted from repr
    and comparisons, and it is never copied to returned evidence.
    """

    endpoint: str
    method: str
    path: str
    authorization_scheme: str
    api_key: str = field(repr=False, compare=False)


@dataclass(frozen=True)
class ArvanEccGetServerRunnerResult:
    """Bounded runner result: only status code and response body are exposed."""

    status_code: int
    body: bytes


class ArvanEccGetServerRunner(Protocol):
    """Future transport seam; implementation must perform only this GET."""

    async def run(
        self, *, invocation: ArvanEccGetServerInvocation
    ) -> ArvanEccGetServerRunnerResult:
        """Perform the immutable GET invocation and return bounded body bytes."""


@dataclass(frozen=True)
class _TargetFacts:
    role: str
    instance_id: str
    public_ipv4: str
    region: str


@dataclass(frozen=True)
class _SecretConfig:
    api_key: str = field(repr=False, compare=False)


def _fail(code: str) -> None:
    raise ArvanEccProviderReadbackError(code)


def _safe_text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or "\x00" in value
        or pattern.fullmatch(value) is None
        or _URL_OR_SECRET_RE.search(value) is not None
    ):
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _canonical_uuid(value: object, *, code: str) -> str:
    text = _safe_text(value, pattern=_SAFE_TEXT_RE, code=code)
    try:
        parsed = UUID(text)
    except (TypeError, ValueError, AttributeError):
        _fail(code)
    if str(parsed) != text or parsed.int == 0:
        _fail(code)
    return text


def _public_ipv4(value: object, *, code: str) -> str:
    text = _safe_text(value, pattern=_SAFE_TEXT_RE, code=code)
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        _fail(code)
    if address.version != 4 or not address.is_global or str(address) != text:
        _fail(code)
    return text


def _validate_enabled_config(value: object) -> None:
    if type(value) is not RootOwnedArvanEccProviderReadbackConfig:
        _fail("ARVAN_ECC_READBACK_CONFIG_INVALID")
    if value.schema != ARVAN_ECC_READBACK_CONFIG_SCHEMA or type(value.enabled) is not bool:
        _fail("ARVAN_ECC_READBACK_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("ARVAN_ECC_READBACK_DISABLED")
    if os.geteuid() != 0:
        _fail("ARVAN_ECC_READBACK_ROOT_RUNTIME_REQUIRED")


def _target_facts(value: object) -> _TargetFacts:
    if type(value) is not DedicatedHostTarget:
        _fail("ARVAN_ECC_READBACK_TARGET_INVALID")
    if value.role not in ROLE_ORDER:
        _fail("ARVAN_ECC_READBACK_TARGET_INVALID")
    expected = EXPECTED_HOSTS[value.role]
    instance_id = _canonical_uuid(value.instance_id, code="ARVAN_ECC_READBACK_TARGET_INVALID")
    public_ipv4 = _public_ipv4(value.public_ipv4, code="ARVAN_ECC_READBACK_TARGET_INVALID")
    region = _safe_text(value.region, pattern=_SAFE_TEXT_RE, code="ARVAN_ECC_READBACK_TARGET_INVALID")
    route, phase = DELIVERY_CONTRACT_BY_ROLE[value.role]
    if (
        instance_id != expected["instance_id"]
        or public_ipv4 != expected["public_ip"]
        or region != expected["region"]
        or value.delivery_route != route
        or value.delivery_phase != phase
        or type(value.host_key_sha256) is not str
        or HEX64.fullmatch(value.host_key_sha256) is None
        or value.host_key_sha256 == "0" * 64
    ):
        _fail("ARVAN_ECC_READBACK_TARGET_SOURCE_PIN_MISMATCH")
    return _TargetFacts(
        role=value.role,
        instance_id=instance_id,
        public_ipv4=public_ipv4,
        region=region,
    )


def _fixed_path(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or any(
        part in {"", ".", ".."} for part in value.parts[1:]
    ):
        _fail(code)
    return value


def _validate_root_ancestors(path: Path, *, code: str) -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail(code)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        for component in path.parts[1:-1]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            metadata = os.fstat(descriptor)
            mode = stat.S_IMODE(metadata.st_mode)
            sticky_root_parent = metadata.st_uid == 0 and bool(metadata.st_mode & stat.S_ISVTX)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or (mode & 0o022 and not sticky_root_parent)
            ):
                _fail(code)
    except ArvanEccProviderReadbackError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_root_private_file(path: Path, *, maximum_bytes: int, code: str) -> bytes:
    path = _fixed_path(path, code=code)
    _validate_root_ancestors(path, code=code)
    if not hasattr(os, "O_NOFOLLOW"):
        _fail(code)
    try:
        before = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or not 1 <= before.st_size <= maximum_bytes
    ):
        _fail(code)
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(descriptor)
        fingerprint = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
        )
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mode,
            opened.st_uid,
            opened.st_nlink,
        ) != fingerprint:
            _fail(code)
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                _fail(code)
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
        ) != fingerprint:
            _fail(code)
        return b"".join(chunks)
    except ArvanEccProviderReadbackError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _strict_json_object(pairs: list[tuple[str, Any]], *, code: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(code)
        result[key] = value
    return result


def _parse_fixed_secret_config(raw: bytes) -> _SecretConfig:
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=lambda pairs: _strict_json_object(
                pairs, code="ARVAN_ECC_READBACK_SECRET_CONFIG_INVALID"
            ),
            parse_constant=lambda _value: _fail("ARVAN_ECC_READBACK_SECRET_CONFIG_INVALID"),
        )
    except ArvanEccProviderReadbackError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("ARVAN_ECC_READBACK_SECRET_CONFIG_INVALID")
    if (
        type(value) is not dict
        or set(value) != {"schema", "enabled", "api_key"}
        or raw != canonical_json_bytes(value) + b"\n"
        or value["schema"] != ARVAN_ECC_READBACK_SECRET_CONFIG_SCHEMA
        or value["enabled"] is not True
        or type(value["api_key"]) is not str
        or _API_KEY_RE.fullmatch(value["api_key"]) is None
    ):
        _fail("ARVAN_ECC_READBACK_SECRET_CONFIG_INVALID")
    return _SecretConfig(api_key=value["api_key"])


def _load_fixed_secret_config() -> _SecretConfig:
    return _parse_fixed_secret_config(
        _read_root_private_file(
            _fixed_path(
                FIXED_ARVAN_ECC_READBACK_CONFIG_FILE,
                code="ARVAN_ECC_READBACK_SECRET_CONFIG_INVALID",
            ),
            maximum_bytes=_MAX_SECRET_CONFIG_BYTES,
            code="ARVAN_ECC_READBACK_SECRET_CONFIG_INVALID",
        )
    )


def _parse_ecc_response(raw: object) -> dict[str, Any]:
    if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_ECC_RESPONSE_BYTES:
        _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=lambda pairs: _strict_json_object(
                pairs, code="ARVAN_ECC_READBACK_RESPONSE_INVALID"
            ),
            parse_constant=lambda _value: _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID"),
        )
    except ArvanEccProviderReadbackError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
    if type(value) is not dict:
        _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
    _reject_secret_or_url_content(value, depth=0)
    return value


def _reject_secret_or_url_content(value: object, *, depth: int) -> None:
    if depth > _MAX_JSON_DEPTH:
        _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
    if isinstance(value, Mapping):
        if len(value) > _MAX_JSON_MEMBERS:
            _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
        for key, item in value.items():
            if type(key) is not str or not key or len(key) > 128:
                _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if any(part in normalized for part in _FORBIDDEN_RESPONSE_KEY_PARTS):
                _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
            _reject_secret_or_url_content(item, depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > _MAX_JSON_MEMBERS:
            _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
        for item in value:
            _reject_secret_or_url_content(item, depth=depth + 1)
        return
    if isinstance(value, str) and (
        "\x00" in value
        or "\r" in value
        or "\n" in value
        or _URL_OR_SECRET_RE.search(value) is not None
    ):
        _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")


def _server_document(value: dict[str, Any]) -> Mapping[str, Any]:
    # Current ECC server-detail responses are direct objects.  A one-field
    # envelope is accepted only to keep the parser fail-closed across the
    # provider's documented response wrapper variants.
    if set(value) == {"data"}:
        value = value["data"]
    if not isinstance(value, Mapping):
        _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
    if not {"id", "status", "addresses"}.issubset(value):
        _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
    return value


def _has_only_expected_public_ipv4(addresses: object, *, target: _TargetFacts) -> None:
    if not isinstance(addresses, Mapping) or not addresses or len(addresses) > _MAX_JSON_MEMBERS:
        _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
    found = False
    for network_name, entries in addresses.items():
        _safe_text(network_name, pattern=_SAFE_TEXT_RE, code="ARVAN_ECC_READBACK_RESPONSE_INVALID")
        if not isinstance(entries, list) or not entries or len(entries) > _MAX_JSON_MEMBERS:
            _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
        for entry in entries:
            if not isinstance(entry, Mapping) or not {"addr", "is_public", "version"}.issubset(entry):
                _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
            if type(entry["is_public"]) is not bool:
                _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
            version = entry["version"]
            if version not in {"4", 4, "6", 6}:
                _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
            address = entry["addr"]
            if type(address) is not str or not address or "\x00" in address:
                _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
            try:
                parsed_address = ipaddress.ip_address(address)
            except ValueError:
                _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
            if str(parsed_address) != address:
                _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
            if entry["is_public"]:
                # This adapter is an identity proof, not an inventory browser:
                # every public address must be the exact source-pinned IPv4.
                if version not in {"4", 4} or _public_ipv4(
                    address, code="ARVAN_ECC_READBACK_RESPONSE_INVALID"
                ) != target.public_ipv4:
                    _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
                found = True
    if not found:
        _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")


def _normalise_ecc_server(value: dict[str, Any], *, target: _TargetFacts) -> dict[str, str]:
    server = _server_document(value)
    if _canonical_uuid(server["id"], code="ARVAN_ECC_READBACK_RESPONSE_INVALID") != target.instance_id:
        _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
    if server["status"] != "ACTIVE":
        _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
    # ECC obtains the region from the hard-pinned request path.  If a response
    # also carries a region field, it must agree exactly rather than override it.
    if "region" in server and _safe_text(
        server["region"], pattern=_SAFE_TEXT_RE, code="ARVAN_ECC_READBACK_RESPONSE_INVALID"
    ) != target.region:
        _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
    _has_only_expected_public_ipv4(server["addresses"], target=target)
    return {
        "schema": PROVIDER_READBACK_SCHEMA,
        "role": target.role,
        "provider": PROVIDER_NAME,
        "instance_id": target.instance_id,
        "public_ipv4": target.public_ipv4,
        "region": target.region,
        "status": "running",
    }


class RootOwnedArvanEccProviderReadback:
    """Default-off fixed GET-only implementation of ``ProviderReadback``."""

    def __init__(
        self,
        *,
        config: RootOwnedArvanEccProviderReadbackConfig = RootOwnedArvanEccProviderReadbackConfig(),
        runner: ArvanEccGetServerRunner | None = None,
    ) -> None:
        self._config = config
        self._runner = runner

    async def readback(self, *, target: DedicatedHostTarget) -> Mapping[str, Any]:
        """Return controller-compatible canonical evidence for one pinned host."""

        _validate_enabled_config(self._config)
        facts = _target_facts(target)
        secret = _load_fixed_secret_config()
        if self._runner is None or not callable(getattr(self._runner, "run", None)):
            _fail("ARVAN_ECC_READBACK_RUNNER_REQUIRED")
        invocation = ArvanEccGetServerInvocation(
            endpoint=FIXED_ARVAN_ECC_ENDPOINT,
            method=_FIXED_METHOD,
            path=f"/regions/{facts.region}/servers/{facts.instance_id}",
            authorization_scheme=_FIXED_AUTH_SCHEME,
            api_key=secret.api_key,
        )
        try:
            runner_result = await self._runner.run(invocation=invocation)
        except Exception:
            _fail("ARVAN_ECC_READBACK_RUNNER_FAILED")
        if (
            type(runner_result) is not ArvanEccGetServerRunnerResult
            or type(runner_result.status_code) is not int
            or runner_result.status_code != 200
            or type(runner_result.body) is not bytes
            or not 1 <= len(runner_result.body) <= _MAX_ECC_RESPONSE_BYTES
        ):
            _fail("ARVAN_ECC_READBACK_RESPONSE_INVALID")
        document = _normalise_ecc_server(_parse_ecc_response(runner_result.body), target=facts)
        raw = canonical_json_bytes(document) + b"\n"
        return {
            "schema": PROVIDER_READBACK_RESPONSE_SCHEMA,
            "role": facts.role,
            "provider": PROVIDER_NAME,
            "readback_mode": PROVIDER_READBACK_MODE,
            "readback_path": PROVIDER_READBACK_PATH_BY_ROLE[facts.role],
            "readback_sha256": hashlib.sha256(raw).hexdigest(),
            "readback_bytes": raw,
        }
