"""Pinned, root-only SSH receipt-delivery adapter for disposable FI roles.

This is a deliberately narrow implementation of the controller's
``pinned-ssh-readonly-agent`` delivery contract.  It never imports or calls a
process, network, SSH library, shell, provider API, or controller runner.  An
installed adapter may inject one async runner that directly executes the
immutable invocation produced here.  Importing or constructing the adapter is
inert.

Only ``bot_fi``, ``webapp_fi`` and ``witness`` are supported.  ``webapp_ir``
is categorically rejected: its controller contract is private Object-Storage
pull, never direct SSH.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
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
    AGENT_DELIVERY_RESPONSE_SCHEMA,
    DELIVERY_CONTRACT_BY_ROLE,
    RECEIPT_PATH_BY_ROLE,
    DedicatedHostTarget,
)
from core.dedicated_host_preflight_receipt import (
    CAMPAIGN_ID,
    HEX40,
    HEX64,
    MAX_RECEIPT_BYTES,
    canonical_json_bytes,
    parse_preflight_receipt,
)
from scripts.dedicated_host_preflight_manifest import (
    EXPECTED_HOSTS,
    READONLY_REQUEST_SCHEMA,
)


__all__ = (
    "FIXED_DEDICATED_HOST_PREFLIGHT_KNOWN_HOSTS",
    "FIXED_DEDICATED_HOST_PREFLIGHT_IDENTITY_FILE",
    "FIXED_PINNED_SSH_BINARY",
    "PINNED_SSH_READONLY_DELIVERY_DEFAULT_ENABLED",
    "PINNED_SSH_READONLY_DELIVERY_SCHEMA",
    "DedicatedHostPreflightPinnedSshDeliveryError",
    "PinnedSshReadonlyAgentDelivery",
    "PinnedSshReadonlyDeliveryConfig",
    "PinnedSshReadonlyInvocation",
    "PinnedSshReadonlyRunner",
    "PinnedSshReadonlyRunnerResult",
)


PINNED_SSH_READONLY_DELIVERY_SCHEMA = (
    "three-site-dedicated-host-preflight-pinned-ssh-delivery-v1"
)
PINNED_SSH_READONLY_DELIVERY_DEFAULT_ENABLED = False

FIXED_PINNED_SSH_BINARY = Path("/usr/bin/ssh")
FIXED_DEDICATED_HOST_PREFLIGHT_KNOWN_HOSTS = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/known_hosts"
)
FIXED_DEDICATED_HOST_PREFLIGHT_IDENTITY_FILE = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/identity_ed25519"
)

_ALLOWED_ROLES = ("bot_fi", "webapp_fi", "witness")
_FIXED_USER = "preflight"
_FIXED_PORT = 22
_FIXED_REMOTE_COMMAND = "collect-readonly-receipt"
_FIXED_CONNECT_TIMEOUT_SECONDS = 5
_MAX_KNOWN_HOSTS_BYTES = 64 * 1024
_MAX_IDENTITY_FILE_BYTES = 64 * 1024
_MAX_SSH_BINARY_BYTES = 128 * 1024 * 1024
_SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$", re.ASCII)
_HOST_KEY_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_KNOWN_HOST_KEY_TYPES = frozenset(
    {"ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa"}
)
_PINNED_KNOWN_HOSTS_ROLE_ORDER = _ALLOWED_ROLES
_PINNED_KNOWN_HOSTS_HOSTS = tuple(
    EXPECTED_HOSTS[role]["public_ip"] for role in _PINNED_KNOWN_HOSTS_ROLE_ORDER
)
_URL_OR_SECRET_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.|bearer\s+|access[_ -]?key|"
    r"authorization|credential|password|private[_ -]?key|secret|token)"
)


class DedicatedHostPreflightPinnedSshDeliveryError(ValueError):
    """A fixed-code local SSH-delivery policy failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PinnedSshReadonlyDeliveryConfig:
    """No caller-selected host, user, command, path, port, or credential."""

    enabled: bool = PINNED_SSH_READONLY_DELIVERY_DEFAULT_ENABLED
    connect_timeout_seconds: int = _FIXED_CONNECT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class PinnedSshReadonlyInvocation:
    """Immutable, redaction-safe runner input for one fixed receipt request."""

    ssh_binary: Path
    arguments: tuple[str, ...]
    stdin_bytes: bytes
    environment: tuple[tuple[str, str], ...]
    known_hosts: Path
    identity_file: Path
    role: str
    host_key_sha256: str
    request_sha256: str


@dataclass(frozen=True)
class PinnedSshReadonlyRunnerResult:
    """The runner may expose only status and stdout receipt bytes."""

    exit_code: int
    stdout_bytes: bytes


class PinnedSshReadonlyRunner(Protocol):
    async def run(
        self,
        *,
        invocation: PinnedSshReadonlyInvocation,
    ) -> PinnedSshReadonlyRunnerResult: ...


@dataclass(frozen=True)
class _TargetFacts:
    role: str
    instance_id: str
    public_ipv4: str
    region: str
    host_key_sha256: str


@dataclass(frozen=True)
class _RequestFacts:
    raw: bytes
    campaign_id: str
    operation_id: str
    release_sha: str
    manifest_sha256: str


def _fail(code: str) -> None:
    raise DedicatedHostPreflightPinnedSshDeliveryError(code)


def _safe_text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        _fail(code)
    if pattern.fullmatch(value) is None or _URL_OR_SECRET_RE.search(value) is not None:
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    result = _safe_text(value, pattern=_HOST_KEY_SHA256_RE, code=code)
    if result == "0" * 64:
        _fail(code)
    return result


def _fixed_path(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or any(
        part in {"", ".", ".."} for part in value.parts[1:]
    ):
        _fail(code)
    return value


def _validate_ancestors(path: Path, *, code: str) -> None:
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
    except DedicatedHostPreflightPinnedSshDeliveryError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_private_file(path: Path, *, maximum_bytes: int, code: str) -> bytes:
    _validate_ancestors(path, code=code)
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
        before_fingerprint = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
        )
        opened_fingerprint = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mode,
            opened.st_uid,
            opened.st_nlink,
        )
        if opened_fingerprint != before_fingerprint:
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
        ) != opened_fingerprint:
            _fail(code)
        return b"".join(chunks)
    except DedicatedHostPreflightPinnedSshDeliveryError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_ssh_binary() -> Path:
    path = _fixed_path(FIXED_PINNED_SSH_BINARY, code="PINNED_SSH_BINARY_INVALID")
    _validate_ancestors(path, code="PINNED_SSH_BINARY_UNAVAILABLE")
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("PINNED_SSH_BINARY_UNAVAILABLE")
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not metadata.st_mode & stat.S_IXUSR
        or not 1 <= metadata.st_size <= _MAX_SSH_BINARY_BYTES
    ):
        _fail("PINNED_SSH_BINARY_UNAVAILABLE")
    return resolved


def _validate_config(value: object) -> PinnedSshReadonlyDeliveryConfig:
    if type(value) is not PinnedSshReadonlyDeliveryConfig:
        _fail("PINNED_SSH_CONFIG_INVALID")
    if type(value.enabled) is not bool:
        _fail("PINNED_SSH_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("PINNED_SSH_DISABLED")
    if (
        type(value.connect_timeout_seconds) is not int
        or value.connect_timeout_seconds != _FIXED_CONNECT_TIMEOUT_SECONDS
    ):
        _fail("PINNED_SSH_CONFIG_INVALID")
    if os.geteuid() != 0:
        _fail("PINNED_SSH_ROOT_RUNTIME_REQUIRED")
    return value


def _target_facts(value: object) -> _TargetFacts:
    if type(value) is not DedicatedHostTarget:
        _fail("PINNED_SSH_TARGET_INVALID")
    if value.role == "webapp_ir":
        _fail("PINNED_SSH_WEBAPP_IR_OBJECT_STORAGE_PULL_REQUIRED")
    if value.role not in _ALLOWED_ROLES:
        _fail("PINNED_SSH_ROLE_FORBIDDEN")
    expected_route, expected_phase = DELIVERY_CONTRACT_BY_ROLE[value.role]
    if (
        value.delivery_route != expected_route
        or value.delivery_phase != expected_phase
        or expected_route != "pinned-ssh-readonly-agent"
        or expected_phase != _FIXED_REMOTE_COMMAND
    ):
        _fail("PINNED_SSH_TARGET_ROUTE_INVALID")
    instance_id = _safe_text(
        value.instance_id,
        pattern=_SAFE_TEXT_RE,
        code="PINNED_SSH_TARGET_INVALID",
    )
    try:
        parsed = UUID(instance_id)
    except (TypeError, ValueError, AttributeError):
        _fail("PINNED_SSH_TARGET_INVALID")
    if str(parsed) != instance_id or parsed.int == 0:
        _fail("PINNED_SSH_TARGET_INVALID")
    public_ipv4 = _safe_text(
        value.public_ipv4,
        pattern=_SAFE_TEXT_RE,
        code="PINNED_SSH_TARGET_INVALID",
    )
    try:
        address = ipaddress.ip_address(public_ipv4)
    except ValueError:
        _fail("PINNED_SSH_TARGET_INVALID")
    if address.version != 4 or not address.is_global or str(address) != public_ipv4:
        _fail("PINNED_SSH_TARGET_INVALID")
    expected = EXPECTED_HOSTS[value.role]
    if (
        instance_id != expected["instance_id"]
        or public_ipv4 != expected["public_ip"]
        or value.region != expected["region"]
    ):
        _fail("PINNED_SSH_TARGET_SOURCE_PIN_MISMATCH")
    return _TargetFacts(
        role=value.role,
        instance_id=instance_id,
        public_ipv4=public_ipv4,
        region=_safe_text(
            value.region,
            pattern=_SAFE_TEXT_RE,
            code="PINNED_SSH_TARGET_INVALID",
        ),
        host_key_sha256=_sha256(
            value.host_key_sha256,
            code="PINNED_SSH_HOST_KEY_PIN_INVALID",
        ),
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("PINNED_SSH_REQUEST_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    _fail("PINNED_SSH_REQUEST_INVALID")


def _validate_request(
    *,
    request_bytes: object,
    request_sha256: object,
    target: _TargetFacts,
) -> _RequestFacts:
    if (
        type(request_bytes) is not bytes
        or not request_bytes
        or len(request_bytes) > MAX_RECEIPT_BYTES
    ):
        _fail("PINNED_SSH_REQUEST_INVALID")
    if _sha256(
        request_sha256, code="PINNED_SSH_REQUEST_HASH_INVALID"
    ) != hashlib.sha256(request_bytes).hexdigest():
        _fail("PINNED_SSH_REQUEST_HASH_INVALID")
    try:
        value = json.loads(
            request_bytes.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except DedicatedHostPreflightPinnedSshDeliveryError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail("PINNED_SSH_REQUEST_INVALID")
    if type(value) is not dict or canonical_json_bytes(value) + b"\n" != request_bytes:
        _fail("PINNED_SSH_REQUEST_INVALID")
    fields = {
        "schema",
        "campaign_id",
        "operation_id",
        "release_sha",
        "role",
        "manifest_sha256",
    }
    if set(value) != fields or value["schema"] != READONLY_REQUEST_SCHEMA:
        _fail("PINNED_SSH_REQUEST_INVALID")
    campaign_id = _safe_text(
        value["campaign_id"], pattern=CAMPAIGN_ID, code="PINNED_SSH_REQUEST_INVALID"
    )
    operation_id = _safe_text(
        value["operation_id"],
        pattern=_SAFE_TEXT_RE,
        code="PINNED_SSH_REQUEST_INVALID",
    )
    try:
        parsed = UUID(operation_id)
    except (TypeError, ValueError, AttributeError):
        _fail("PINNED_SSH_REQUEST_INVALID")
    if str(parsed) != operation_id or parsed.int == 0:
        _fail("PINNED_SSH_REQUEST_INVALID")
    release_sha = _safe_text(
        value["release_sha"], pattern=HEX40, code="PINNED_SSH_REQUEST_INVALID"
    )
    manifest_sha256 = _safe_text(
        value["manifest_sha256"], pattern=HEX64, code="PINNED_SSH_REQUEST_INVALID"
    )
    if value["role"] != target.role:
        _fail("PINNED_SSH_REQUEST_INVALID")
    return _RequestFacts(
        raw=request_bytes,
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        manifest_sha256=manifest_sha256,
    )


def _validate_receipt_path(value: object, *, target: _TargetFacts) -> str:
    if type(value) is not str or value != RECEIPT_PATH_BY_ROLE[target.role]:
        _fail("PINNED_SSH_RECEIPT_PATH_INVALID")
    return value


def _parse_known_hosts(*, raw: bytes, target: _TargetFacts) -> None:
    try:
        text = raw.decode("ascii", "strict")
    except UnicodeDecodeError:
        _fail("PINNED_SSH_KNOWN_HOSTS_INVALID")
    if not text.endswith("\n") or "\r" in text or "\x00" in text:
        _fail("PINNED_SSH_KNOWN_HOSTS_INVALID")
    entries: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line != line.strip() or line.startswith("#"):
            _fail("PINNED_SSH_KNOWN_HOSTS_INVALID")
        parts = line.split(" ")
        if len(parts) != 3 or any(not part for part in parts):
            _fail("PINNED_SSH_KNOWN_HOSTS_INVALID")
        host, key_type, encoded = parts
        if (
            host not in _PINNED_KNOWN_HOSTS_HOSTS
            or key_type not in _KNOWN_HOST_KEY_TYPES
            or host in entries
            or any(marker in host for marker in (",", "*", "?", "!", "|", "[", "]"))
        ):
            _fail("PINNED_SSH_KNOWN_HOSTS_INVALID")
        try:
            key_blob = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error):
            _fail("PINNED_SSH_KNOWN_HOSTS_INVALID")
        if not key_blob or len(key_blob) > 16 * 1024:
            _fail("PINNED_SSH_KNOWN_HOSTS_INVALID")
        if len(key_blob) < 5:
            _fail("PINNED_SSH_KNOWN_HOSTS_INVALID")
        key_type_length = int.from_bytes(key_blob[:4], "big")
        key_type_end = 4 + key_type_length
        if (
            key_type_end > len(key_blob)
            or key_blob[4:key_type_end] != key_type.encode("ascii")
        ):
            _fail("PINNED_SSH_KNOWN_HOSTS_INVALID")
        entries[host] = hashlib.sha256(key_blob).hexdigest()
    if (
        tuple(entries) != _PINNED_KNOWN_HOSTS_HOSTS
        or entries[target.public_ipv4] != target.host_key_sha256
    ):
        _fail("PINNED_SSH_HOST_KEY_PIN_MISMATCH")


def _known_hosts_for_target(target: _TargetFacts) -> Path:
    path = _fixed_path(
        FIXED_DEDICATED_HOST_PREFLIGHT_KNOWN_HOSTS,
        code="PINNED_SSH_KNOWN_HOSTS_INVALID",
    )
    raw = _read_private_file(
        path,
        maximum_bytes=_MAX_KNOWN_HOSTS_BYTES,
        code="PINNED_SSH_KNOWN_HOSTS_INVALID",
    )
    _parse_known_hosts(raw=raw, target=target)
    return path


def _identity_file() -> Path:
    """Require one fixed root-only SSH identity without exposing its bytes.

    The prior invocation intentionally had no generic key selector, but that
    still allowed OpenSSH to search ambient default identity locations.  A
    concrete runner must never inherit that authority.  The fixed file is
    opened only for the existing anti-symlink/root-ownership verification;
    its material is discarded immediately and never reaches an invocation,
    receipt, exception, or log surface.
    """

    path = _fixed_path(
        FIXED_DEDICATED_HOST_PREFLIGHT_IDENTITY_FILE,
        code="PINNED_SSH_IDENTITY_FILE_INVALID",
    )
    _read_private_file(
        path,
        maximum_bytes=_MAX_IDENTITY_FILE_BYTES,
        code="PINNED_SSH_IDENTITY_FILE_INVALID",
    )
    return path


class PinnedSshReadonlyAgentDelivery:
    """One injectable, root-only agent delivery adapter for the three FI roles."""

    def __init__(
        self,
        *,
        config: PinnedSshReadonlyDeliveryConfig = PinnedSshReadonlyDeliveryConfig(),
        runner: PinnedSshReadonlyRunner | None = None,
    ) -> None:
        self._config = config
        self._runner = runner

    async def collect_readonly_receipt(
        self,
        *,
        target: DedicatedHostTarget,
        request_bytes: bytes,
        request_sha256: str,
        receipt_path: str,
    ) -> Mapping[str, Any]:
        """Return only controller-compatible metadata plus canonical receipt bytes."""

        _validate_config(self._config)
        facts = _target_facts(target)
        request = _validate_request(
            request_bytes=request_bytes,
            request_sha256=request_sha256,
            target=facts,
        )
        exact_receipt_path = _validate_receipt_path(receipt_path, target=facts)
        if self._runner is None or not callable(getattr(self._runner, "run", None)):
            _fail("PINNED_SSH_RUNNER_REQUIRED")
        known_hosts = _known_hosts_for_target(facts)
        identity_file = _identity_file()
        ssh_binary = _validate_ssh_binary()
        invocation = PinnedSshReadonlyInvocation(
            ssh_binary=ssh_binary,
            arguments=(
                str(ssh_binary),
                "-F",
                "/dev/null",
                "-i",
                str(identity_file),
                "-o",
                "BatchMode=yes",
                "-o",
                "PasswordAuthentication=no",
                "-o",
                "KbdInteractiveAuthentication=no",
                "-o",
                "ChallengeResponseAuthentication=no",
                "-o",
                "PubkeyAuthentication=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "IdentityAgent=none",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "UserKnownHostsFile=" + str(known_hosts),
                "-o",
                "GlobalKnownHostsFile=/dev/null",
                "-o",
                "UpdateHostKeys=no",
                "-o",
                "HashKnownHosts=no",
                "-o",
                "ForwardAgent=no",
                "-o",
                "ClearAllForwardings=yes",
                "-o",
                "PermitLocalCommand=no",
                "-o",
                "RequestTTY=no",
                "-o",
                "ConnectTimeout=" + str(_FIXED_CONNECT_TIMEOUT_SECONDS),
                "-o",
                "ConnectionAttempts=1",
                "-p",
                str(_FIXED_PORT),
                _FIXED_USER + "@" + facts.public_ipv4,
                _FIXED_REMOTE_COMMAND,
            ),
            stdin_bytes=request.raw,
            environment=(),
            known_hosts=known_hosts,
            identity_file=identity_file,
            role=facts.role,
            host_key_sha256=facts.host_key_sha256,
            request_sha256=request_sha256,
        )
        try:
            runner_result = await self._runner.run(invocation=invocation)
        except Exception:
            _fail("PINNED_SSH_RUNNER_FAILED")
        if (
            type(runner_result) is not PinnedSshReadonlyRunnerResult
            or type(runner_result.exit_code) is not int
            or runner_result.exit_code != 0
            or type(runner_result.stdout_bytes) is not bytes
            or not runner_result.stdout_bytes
            or len(runner_result.stdout_bytes) > MAX_RECEIPT_BYTES
        ):
            _fail("PINNED_SSH_RUNNER_FAILED")
        try:
            parse_preflight_receipt(
                runner_result.stdout_bytes,
                expected_role=facts.role,
                expected_campaign_id=request.campaign_id,
                expected_operation_id=request.operation_id,
                expected_instance_id=facts.instance_id,
                expected_manifest_sha256=request.manifest_sha256,
            )
        except Exception:
            _fail("PINNED_SSH_RECEIPT_INVALID")
        return {
            "schema": AGENT_DELIVERY_RESPONSE_SCHEMA,
            "role": facts.role,
            "delivery_route": "pinned-ssh-readonly-agent",
            "delivery_phase": _FIXED_REMOTE_COMMAND,
            "host_key_sha256": facts.host_key_sha256,
            "request_sha256": request_sha256,
            "receipt_path": exact_receipt_path,
            "receipt_sha256": hashlib.sha256(runner_result.stdout_bytes).hexdigest(),
            "receipt_bytes": runner_result.stdout_bytes,
        }
