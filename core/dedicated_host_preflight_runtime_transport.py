"""Concrete, default-off transport runners for the dedicated-host preflight.

This module is deliberately a *small runtime boundary*.  The controller and
its individual adapter contracts remain pure and transport-injected.  Nothing
in this module runs at import or construction time; a caller must first load a
canonical root-owned runtime configuration, explicitly enable it, run as root,
and admit the separate central Witness-evidence verifier policy.  That policy
contains only public dual-signature pins and a fixed request; it has no WA-IR
secret, Object-Storage locator, receiver credential, or age identity.

The only live operations represented here are:

* a fixed, direct HTTPS GET to the Arvan ECC server-detail endpoint; and
* a fixed argv-only SSH receipt collection for Bot-FI, WA-FI, and Witness; and
* a different fixed argv-only SSH evidence read from Witness for WA-IR.

WA-IR is never contacted by an SSH runner.  Its controller branch reaches
only the separate Witness account/command and verifies both signatures before
the inner v2 receipt reaches the aggregate.  This module never creates a
direct Finland-to-Iran connection, Object-Storage preflight provisioning,
URL, proxy, shell command, or deployment action.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import hashlib
import http.client
import os
from pathlib import Path
import re
import ssl
from typing import Any, Protocol

from core import dedicated_host_preflight_arvan_ecc_readback as _ecc
from core import dedicated_host_preflight_pinned_ssh_delivery as _ssh
from core import dedicated_host_preflight_witness_evidence_pinned_ssh_delivery as _witness_ssh
from core import dedicated_host_preflight_witness_evidence_runtime as _witness_runtime
from core.dedicated_host_preflight_controller import (
    AgentDelivery,
    DedicatedHostTarget,
    ProviderReadback,
)
from scripts.dedicated_host_preflight_manifest import EXPECTED_HOSTS


__all__ = (
    "DEDICATED_HOST_PREFLIGHT_RUNTIME_TRANSPORT_CONFIG_SCHEMA",
    "DEDICATED_HOST_PREFLIGHT_RUNTIME_TRANSPORT_DEFAULT_ENABLED",
    "DedicatedHostPreflightRuntimeTransportError",
    "RootOwnedDedicatedHostPreflightRuntimeAdapters",
    "RootOwnedDedicatedHostPreflightRuntimeTransportConfig",
    "RootOwnedArvanEccHttpsGetRunner",
    "RootOwnedPinnedSshProcessRunner",
    "RootOwnedWitnessEvidenceSshProcessRunner",
    "assemble_root_owned_dedicated_host_preflight_runtime_adapters",
    "parse_root_owned_dedicated_host_preflight_runtime_transport_config",
)


DEDICATED_HOST_PREFLIGHT_RUNTIME_TRANSPORT_CONFIG_SCHEMA = (
    "three-site-dedicated-host-preflight-runtime-transport-config-v2"
)
DEDICATED_HOST_PREFLIGHT_RUNTIME_TRANSPORT_DEFAULT_ENABLED = False

_RUNTIME_MODE = "read-only"
_PROVIDER_TRANSPORT = "fixed-https-get-only"
_FI_RECEIPT_TRANSPORT = "pinned-ssh-readonly-agent"
_IR_RECEIPT_TRANSPORT = "pinned-ssh-witness-evidence-agent"
_DIRECT_FINLAND_TO_IR = "forbidden"

_SSH_ROLES = ("bot_fi", "webapp_fi", "witness")
_SSH_RUNNER_TIMEOUT_SECONDS = 8
_MAX_SSH_STDERR_BYTES = 8 * 1024
_STREAM_READ_BYTES = 4 * 1024

_ECC_CONNECT_TIMEOUT_SECONDS = 5
_ECC_MAX_RESPONSE_BYTES = 64 * 1024
_ECC_RESPONSE_READ_BYTES = 4 * 1024
_ECC_HOST = "napi.arvancloud.ir"
_ECC_PORT = 443
_ECC_BASE_PATH = "/ecc/v1"
_ECC_PATH_RE = re.compile(
    r"^/regions/([a-z0-9][a-z0-9-]{0,62})/servers/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.ASCII,
)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)

# No caller-controlled environment reaches the SSH child.  It has no agent,
# proxy, locale, home-directory config, or alternate executable lookup.
_FIXED_SSH_EXEC_ENV: Mapping[str, str] = {
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "SSH_ASKPASS_REQUIRE": "never",
}


class DedicatedHostPreflightRuntimeTransportError(ValueError):
    """Fixed-code runtime refusal with no network/process diagnostic text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedDedicatedHostPreflightRuntimeTransportConfig:
    """Non-secret, fixed-route runtime admission policy.

    It deliberately has no URL, hostname, IP, command, path, timeout, proxy,
    credential, release, or Object-Storage selector.  The CLI loads its
    canonical form only from one root-owned 0600 file.
    """

    schema: str = DEDICATED_HOST_PREFLIGHT_RUNTIME_TRANSPORT_CONFIG_SCHEMA
    enabled: bool = DEDICATED_HOST_PREFLIGHT_RUNTIME_TRANSPORT_DEFAULT_ENABLED
    mode: str = _RUNTIME_MODE
    provider_transport: str = _PROVIDER_TRANSPORT
    fi_receipt_transport: str = _FI_RECEIPT_TRANSPORT
    ir_receipt_transport: str = _IR_RECEIPT_TRANSPORT
    direct_finland_to_iran: str = _DIRECT_FINLAND_TO_IR


@dataclass(frozen=True)
class RootOwnedDedicatedHostPreflightRuntimeAdapters:
    """The two controller interfaces after all local runtime gates pass."""

    provider_readback: ProviderReadback
    agent_delivery: AgentDelivery


class _HttpResponse(Protocol):
    status: int

    def read(self, amount: int = -1) -> bytes: ...

    def close(self) -> None: ...


class _HttpsConnection(Protocol):
    def request(
        self,
        method: str,
        url: str,
        body: object | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None: ...

    def getresponse(self) -> _HttpResponse: ...

    def close(self) -> None: ...


HttpsConnectionFactory = Callable[..., _HttpsConnection]


class _AsyncReadable(Protocol):
    async def read(self, amount: int = -1) -> bytes: ...


class _SpawnedProcess(Protocol):
    stdout: _AsyncReadable | None
    stderr: _AsyncReadable | None

    async def wait(self) -> int: ...

    def kill(self) -> None: ...


ProcessLauncher = Callable[..., Awaitable[_SpawnedProcess]]


def _fail(code: str) -> None:
    raise DedicatedHostPreflightRuntimeTransportError(code)


def _require_root(*, code: str) -> None:
    try:
        is_root = os.geteuid() == 0
    except OSError:
        is_root = False
    if not is_root:
        _fail(code)


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def parse_root_owned_dedicated_host_preflight_runtime_transport_config(
    value: object,
) -> RootOwnedDedicatedHostPreflightRuntimeTransportConfig:
    """Validate the non-secret canonical runtime document without I/O."""

    item = _exact_mapping(
        value,
        fields=frozenset(
            {
                "schema",
                "enabled",
                "mode",
                "provider_transport",
                "fi_receipt_transport",
                "ir_receipt_transport",
                "direct_finland_to_iran",
            }
        ),
        code="PREFLIGHT_RUNTIME_TRANSPORT_CONFIG_INVALID",
    )
    if (
        item["schema"] != DEDICATED_HOST_PREFLIGHT_RUNTIME_TRANSPORT_CONFIG_SCHEMA
        or type(item["enabled"]) is not bool
        or item["mode"] != _RUNTIME_MODE
        or item["provider_transport"] != _PROVIDER_TRANSPORT
        or item["fi_receipt_transport"] != _FI_RECEIPT_TRANSPORT
        or item["ir_receipt_transport"] != _IR_RECEIPT_TRANSPORT
        or item["direct_finland_to_iran"] != _DIRECT_FINLAND_TO_IR
    ):
        _fail("PREFLIGHT_RUNTIME_TRANSPORT_CONFIG_INVALID")
    return RootOwnedDedicatedHostPreflightRuntimeTransportConfig(
        schema=DEDICATED_HOST_PREFLIGHT_RUNTIME_TRANSPORT_CONFIG_SCHEMA,
        enabled=item["enabled"],
        mode=_RUNTIME_MODE,
        provider_transport=_PROVIDER_TRANSPORT,
        fi_receipt_transport=_FI_RECEIPT_TRANSPORT,
        ir_receipt_transport=_IR_RECEIPT_TRANSPORT,
        direct_finland_to_iran=_DIRECT_FINLAND_TO_IR,
    )


def _enabled_runtime_config(
    value: object,
) -> RootOwnedDedicatedHostPreflightRuntimeTransportConfig:
    if type(value) is not RootOwnedDedicatedHostPreflightRuntimeTransportConfig:
        _fail("PREFLIGHT_RUNTIME_TRANSPORT_CONFIG_INVALID")
    config = parse_root_owned_dedicated_host_preflight_runtime_transport_config(
        {
            "schema": value.schema,
            "enabled": value.enabled,
            "mode": value.mode,
            "provider_transport": value.provider_transport,
            "fi_receipt_transport": value.fi_receipt_transport,
            "ir_receipt_transport": value.ir_receipt_transport,
            "direct_finland_to_iran": value.direct_finland_to_iran,
        }
    )
    if config.enabled is not True:
        _fail("PREFLIGHT_RUNTIME_TRANSPORT_DISABLED")
    return config


def _ecc_expected_path(value: object) -> str:
    if type(value) is not str:
        _fail("ARVAN_ECC_HTTPS_INVOCATION_INVALID")
    matched = _ECC_PATH_RE.fullmatch(value)
    if matched is None:
        _fail("ARVAN_ECC_HTTPS_INVOCATION_INVALID")
    region, instance_id = matched.groups()
    if not any(
        facts["region"] == region and facts["instance_id"] == instance_id
        for facts in EXPECTED_HOSTS.values()
    ):
        _fail("ARVAN_ECC_HTTPS_INVOCATION_INVALID")
    return value


def _validate_ecc_invocation(value: object) -> _ecc.ArvanEccGetServerInvocation:
    if type(value) is not _ecc.ArvanEccGetServerInvocation:
        _fail("ARVAN_ECC_HTTPS_INVOCATION_INVALID")
    if (
        value.endpoint != _ecc.FIXED_ARVAN_ECC_ENDPOINT
        or value.endpoint != f"https://{_ECC_HOST}{_ECC_BASE_PATH}"
        or value.method != "GET"
        or value.authorization_scheme != "Apikey"
        or _ecc._API_KEY_RE.fullmatch(value.api_key) is None  # type: ignore[attr-defined]
    ):
        _fail("ARVAN_ECC_HTTPS_INVOCATION_INVALID")
    _ecc_expected_path(value.path)
    return value


def _tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    # ``create_default_context`` already enables certificate verification and
    # hostname verification.  State the two invariants explicitly so a future
    # refactor cannot silently weaken them.
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _default_https_connection_factory(
    *, host: str, port: int, timeout: int, context: ssl.SSLContext
) -> _HttpsConnection:
    return http.client.HTTPSConnection(host=host, port=port, timeout=timeout, context=context)


def _read_http_body_capped(response: _HttpResponse) -> bytes:
    payload = bytearray()
    while True:
        remaining = _ECC_MAX_RESPONSE_BYTES + 1 - len(payload)
        if remaining <= 0:
            _fail("ARVAN_ECC_HTTPS_RESPONSE_OVERSIZE")
        try:
            chunk = response.read(min(_ECC_RESPONSE_READ_BYTES, remaining))
        except Exception:
            _fail("ARVAN_ECC_HTTPS_REQUEST_FAILED")
        if type(chunk) is not bytes:
            _fail("ARVAN_ECC_HTTPS_RESPONSE_INVALID")
        if not chunk:
            return bytes(payload)
        payload.extend(chunk)
        if len(payload) > _ECC_MAX_RESPONSE_BYTES:
            _fail("ARVAN_ECC_HTTPS_RESPONSE_OVERSIZE")


class RootOwnedArvanEccHttpsGetRunner:
    """Direct, no-proxy, no-redirect, certificate-verified ECC GET runner.

    Tests inject a local connection factory.  The production constructor has
    no URL, proxy, method, header, timeout, or credential parameter.
    """

    def __init__(self, *, connection_factory: HttpsConnectionFactory | None = None) -> None:
        self._connection_factory = (
            _default_https_connection_factory
            if connection_factory is None
            else connection_factory
        )

    def _run_once(
        self, invocation: _ecc.ArvanEccGetServerInvocation
    ) -> _ecc.ArvanEccGetServerRunnerResult:
        connection: _HttpsConnection | None = None
        response: _HttpResponse | None = None
        try:
            connection = self._connection_factory(
                host=_ECC_HOST,
                port=_ECC_PORT,
                timeout=_ECC_CONNECT_TIMEOUT_SECONDS,
                context=_tls_context(),
            )
            # ``http.client`` is a direct TCP/TLS client.  It neither consults
            # proxy environment variables nor implements redirect following.
            connection.request(
                "GET",
                _ECC_BASE_PATH + invocation.path,
                body=None,
                headers={
                    "Accept": "application/json",
                    "Authorization": "Apikey " + invocation.api_key,
                },
            )
            response = connection.getresponse()
            status_code = getattr(response, "status", None)
            if type(status_code) is not int:
                _fail("ARVAN_ECC_HTTPS_RESPONSE_INVALID")
            body = _read_http_body_capped(response)
            return _ecc.ArvanEccGetServerRunnerResult(
                status_code=status_code,
                body=body,
            )
        except DedicatedHostPreflightRuntimeTransportError:
            raise
        except Exception:
            _fail("ARVAN_ECC_HTTPS_REQUEST_FAILED")
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    async def run(
        self, *, invocation: _ecc.ArvanEccGetServerInvocation
    ) -> _ecc.ArvanEccGetServerRunnerResult:
        _require_root(code="ARVAN_ECC_HTTPS_ROOT_RUNTIME_REQUIRED")
        checked = _validate_ecc_invocation(invocation)
        try:
            # The fixed connection has a five-second socket timeout.  Keeping
            # this narrow request synchronous avoids spawning an executor that
            # could outlive a failed close while preserving the controller's
            # sequential, bounded evidence semantics.
            return self._run_once(checked)
        except DedicatedHostPreflightRuntimeTransportError:
            raise
        except Exception:
            _fail("ARVAN_ECC_HTTPS_REQUEST_FAILED")


async def _default_process_launcher(
    *arguments: str, **kwargs: Any
) -> _SpawnedProcess:
    return await asyncio.create_subprocess_exec(*arguments, **kwargs)


def _ssh_target_for_invocation(
    invocation: _ssh.PinnedSshReadonlyInvocation,
) -> tuple[object, object, Path, Path, Path]:
    """Re-derive the exact SSH invocation before any process is started."""

    if type(invocation) is not _ssh.PinnedSshReadonlyInvocation:
        _fail("PINNED_SSH_RUNTIME_INVOCATION_INVALID")
    if invocation.role not in _SSH_ROLES:
        _fail("PINNED_SSH_WEBAPP_IR_OBJECT_STORAGE_PULL_REQUIRED")
    if (
        type(invocation.stdin_bytes) is not bytes
        or not invocation.stdin_bytes
        or len(invocation.stdin_bytes) > _ssh.MAX_RECEIPT_BYTES
        or type(invocation.request_sha256) is not str
        or _HEX64_RE.fullmatch(invocation.request_sha256) is None
        or hashlib.sha256(invocation.stdin_bytes).hexdigest() != invocation.request_sha256
        or type(invocation.host_key_sha256) is not str
        or _HEX64_RE.fullmatch(invocation.host_key_sha256) is None
        or invocation.host_key_sha256 == "0" * 64
        or invocation.environment != ()
    ):
        _fail("PINNED_SSH_RUNTIME_INVOCATION_INVALID")
    expected = EXPECTED_HOSTS[invocation.role]
    route, phase = _ssh.DELIVERY_CONTRACT_BY_ROLE[invocation.role]
    target = _ssh.DedicatedHostTarget(
        role=invocation.role,
        instance_id=expected["instance_id"],
        public_ipv4=expected["public_ip"],
        region=expected["region"],
        host_key_sha256=invocation.host_key_sha256,
        delivery_route=route,
        delivery_phase=phase,
    )
    try:
        facts = _ssh._target_facts(target)  # type: ignore[attr-defined]
        request = _ssh._validate_request(  # type: ignore[attr-defined]
            request_bytes=invocation.stdin_bytes,
            request_sha256=invocation.request_sha256,
            target=facts,
        )
        known_hosts = _ssh._known_hosts_for_target(facts)  # type: ignore[attr-defined]
        identity_file = _ssh._identity_file()  # type: ignore[attr-defined]
        ssh_binary = _ssh._validate_ssh_binary()  # type: ignore[attr-defined]
    except Exception:
        _fail("PINNED_SSH_RUNTIME_INVOCATION_INVALID")
    expected_arguments = (
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
        "ConnectTimeout=5",
        "-o",
        "ConnectionAttempts=1",
        "-p",
        "22",
        "preflight@" + expected["public_ip"],
        "collect-readonly-receipt",
    )
    if (
        invocation.ssh_binary != ssh_binary
        or invocation.known_hosts != known_hosts
        or invocation.identity_file != identity_file
        or invocation.arguments != expected_arguments
    ):
        _fail("PINNED_SSH_RUNTIME_INVOCATION_INVALID")
    return facts, request, ssh_binary, known_hosts, identity_file


async def _read_process_stream_capped(
    stream: _AsyncReadable | None, *, maximum_bytes: int
) -> bytes:
    if stream is None:
        _fail("PINNED_SSH_RUNTIME_PROCESS_INVALID")
    payload = bytearray()
    while True:
        remaining = maximum_bytes + 1 - len(payload)
        if remaining <= 0:
            _fail("PINNED_SSH_RUNTIME_OUTPUT_OVERSIZE")
        try:
            chunk = await stream.read(min(_STREAM_READ_BYTES, remaining))
        except Exception:
            _fail("PINNED_SSH_RUNTIME_PROCESS_FAILED")
        if type(chunk) is not bytes:
            _fail("PINNED_SSH_RUNTIME_PROCESS_INVALID")
        if not chunk:
            return bytes(payload)
        payload.extend(chunk)
        if len(payload) > maximum_bytes:
            _fail("PINNED_SSH_RUNTIME_OUTPUT_OVERSIZE")


async def _terminate_process(process: _SpawnedProcess) -> None:
    try:
        process.kill()
    except Exception:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=1)
    except Exception:
        pass


class RootOwnedPinnedSshProcessRunner:
    """Exact-argv SSH runner with no shell or inherited local authority."""

    def __init__(self, *, process_launcher: ProcessLauncher | None = None) -> None:
        self._process_launcher = (
            _default_process_launcher if process_launcher is None else process_launcher
        )

    async def run(
        self, *, invocation: _ssh.PinnedSshReadonlyInvocation
    ) -> _ssh.PinnedSshReadonlyRunnerResult:
        _require_root(code="PINNED_SSH_ROOT_RUNTIME_REQUIRED")
        _ssh_target_for_invocation(invocation)
        process: _SpawnedProcess | None = None
        tasks: tuple[asyncio.Task[Any], ...] = ()
        completed = False
        try:
            process = await self._process_launcher(
                *invocation.arguments,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=dict(_FIXED_SSH_EXEC_ENV),
                close_fds=True,
                start_new_session=True,
            )
            if process is None:
                _fail("PINNED_SSH_RUNTIME_PROCESS_INVALID")
            stdin = getattr(process, "stdin", None)
            if stdin is None or not callable(getattr(stdin, "write", None)) or not callable(
                getattr(stdin, "close", None)
            ):
                _fail("PINNED_SSH_RUNTIME_PROCESS_INVALID")
            stdin.write(invocation.stdin_bytes)
            stdin.close()
            # Do not await a potentially unbounded local pipe close before
            # starting the bounded reader/wait group below.  The request is
            # already at most 32 KiB and ``close`` delivers EOF; the single
            # operation timeout governs the rest of the exchange.
            tasks = (
                asyncio.create_task(
                    _read_process_stream_capped(
                        process.stdout,
                        maximum_bytes=_ssh.MAX_RECEIPT_BYTES,
                    )
                ),
                asyncio.create_task(
                    _read_process_stream_capped(
                        process.stderr,
                        maximum_bytes=_MAX_SSH_STDERR_BYTES,
                    )
                ),
                asyncio.create_task(process.wait()),
            )
            stdout_bytes, _stderr_bytes, exit_code = await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=_SSH_RUNNER_TIMEOUT_SECONDS,
            )
            if type(exit_code) is not int:
                _fail("PINNED_SSH_RUNTIME_PROCESS_INVALID")
            if exit_code != 0:
                # Never release potentially diagnostic stdout from a failed
                # process to the receipt adapter.
                completed = True
                return _ssh.PinnedSshReadonlyRunnerResult(
                    exit_code=exit_code,
                    stdout_bytes=b"",
                )
            if type(stdout_bytes) is not bytes:
                _fail("PINNED_SSH_RUNTIME_PROCESS_INVALID")
            completed = True
            return _ssh.PinnedSshReadonlyRunnerResult(
                exit_code=0,
                stdout_bytes=stdout_bytes,
            )
        except DedicatedHostPreflightRuntimeTransportError:
            raise
        except Exception:
            _fail("PINNED_SSH_RUNTIME_PROCESS_FAILED")
        finally:
            if process is not None and not completed:
                await _terminate_process(process)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)


async def _read_witness_evidence_process_stream_capped(
    stream: _AsyncReadable | None, *, maximum_bytes: int
) -> bytes:
    if stream is None:
        _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_PROCESS_INVALID")
    payload = bytearray()
    while True:
        remaining = maximum_bytes + 1 - len(payload)
        if remaining <= 0:
            _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_OUTPUT_OVERSIZE")
        try:
            chunk = await stream.read(min(_STREAM_READ_BYTES, remaining))
        except Exception:
            _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_PROCESS_FAILED")
        if type(chunk) is not bytes:
            _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_PROCESS_INVALID")
        if not chunk:
            return bytes(payload)
        payload.extend(chunk)
        if len(payload) > maximum_bytes:
            _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_OUTPUT_OVERSIZE")


class RootOwnedWitnessEvidenceSshProcessRunner:
    """Run only the distinct no-input literal Witness evidence SSH argv."""

    def __init__(self, *, process_launcher: ProcessLauncher | None = None) -> None:
        self._process_launcher = (
            _default_process_launcher if process_launcher is None else process_launcher
        )

    async def run(
        self, *, invocation: _witness_ssh.PinnedSshWitnessEvidenceInvocation
    ) -> _witness_ssh.PinnedSshWitnessEvidenceRunnerResult:
        _require_root(code="PINNED_SSH_WITNESS_EVIDENCE_ROOT_RUNTIME_REQUIRED")
        try:
            _witness_ssh.validate_witness_evidence_ssh_invocation(invocation)
        except Exception as exc:
            raise DedicatedHostPreflightRuntimeTransportError(
                "PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_INVOCATION_INVALID"
            ) from exc
        process: _SpawnedProcess | None = None
        tasks: tuple[asyncio.Task[Any], ...] = ()
        completed = False
        try:
            process = await self._process_launcher(
                *invocation.arguments,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=dict(_FIXED_SSH_EXEC_ENV),
                close_fds=True,
                start_new_session=True,
            )
            if process is None:
                _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_PROCESS_INVALID")
            stdin = getattr(process, "stdin", None)
            if stdin is None or not callable(getattr(stdin, "write", None)) or not callable(
                getattr(stdin, "close", None)
            ):
                _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_PROCESS_INVALID")
            stdin.write(invocation.stdin_bytes)
            stdin.close()
            tasks = (
                asyncio.create_task(
                    _read_witness_evidence_process_stream_capped(
                        process.stdout,
                        maximum_bytes=_witness_ssh.MAX_WITNESS_EVIDENCE_BYTES,
                    )
                ),
                asyncio.create_task(
                    _read_witness_evidence_process_stream_capped(
                        process.stderr,
                        maximum_bytes=_MAX_SSH_STDERR_BYTES,
                    )
                ),
                asyncio.create_task(process.wait()),
            )
            stdout_bytes, _stderr_bytes, exit_code = await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=_SSH_RUNNER_TIMEOUT_SECONDS,
            )
            if type(exit_code) is not int:
                _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_PROCESS_INVALID")
            if exit_code != 0:
                completed = True
                return _witness_ssh.PinnedSshWitnessEvidenceRunnerResult(
                    exit_code=exit_code,
                    stdout_bytes=b"",
                )
            if type(stdout_bytes) is not bytes:
                _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_PROCESS_INVALID")
            completed = True
            return _witness_ssh.PinnedSshWitnessEvidenceRunnerResult(
                exit_code=0,
                stdout_bytes=stdout_bytes,
            )
        except DedicatedHostPreflightRuntimeTransportError:
            raise
        except Exception:
            _fail("PINNED_SSH_WITNESS_EVIDENCE_RUNTIME_PROCESS_FAILED")
        finally:
            if process is not None and not completed:
                await _terminate_process(process)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)


class _RoleBoundAgentDelivery:
    """Dispatch FI receipts locally and WA-IR only via verified Witness evidence."""

    def __init__(
        self,
        *,
        ssh_delivery: _ssh.PinnedSshReadonlyAgentDelivery,
        ir_witness_evidence_delivery: _witness_ssh.PinnedSshWitnessEvidenceAgentDelivery,
    ) -> None:
        self._ssh_delivery = ssh_delivery
        self._ir_witness_evidence_delivery = ir_witness_evidence_delivery

    async def collect_readonly_receipt(
        self,
        *,
        target: DedicatedHostTarget,
        request_bytes: bytes,
        request_sha256: str,
        receipt_path: str,
    ) -> Mapping[str, Any]:
        if type(target) is not DedicatedHostTarget:
            _fail("PREFLIGHT_RUNTIME_TRANSPORT_TARGET_INVALID")
        if target.role == "webapp_ir":
            return await self._ir_witness_evidence_delivery.collect_readonly_receipt(
                target=target,
                request_bytes=request_bytes,
                request_sha256=request_sha256,
                receipt_path=receipt_path,
            )
        if target.role in _SSH_ROLES:
            return await self._ssh_delivery.collect_readonly_receipt(
                target=target,
                request_bytes=request_bytes,
                request_sha256=request_sha256,
                receipt_path=receipt_path,
            )
        _fail("PREFLIGHT_RUNTIME_TRANSPORT_TARGET_INVALID")


def assemble_root_owned_dedicated_host_preflight_runtime_adapters(
    *,
    config: RootOwnedDedicatedHostPreflightRuntimeTransportConfig,
    witness_target: DedicatedHostTarget | None = None,
) -> RootOwnedDedicatedHostPreflightRuntimeAdapters:
    """Assemble concrete runners only after all four route contracts exist.

    The central verifier policy and the source-pinned Witness target must both
    be admitted before any ECC request or SSH process can start.  The verifier
    policy has only the canonical WA-IR attestation request plus two public
    keys; it cannot read WA-IR Object-Storage/age material or contact WA-IR.
    This prevents a partial FI observation from becoming an accidental
    substitute for the required four-role preflight.
    """

    _require_root(code="PREFLIGHT_RUNTIME_TRANSPORT_ROOT_REQUIRED")
    _enabled_runtime_config(config)
    if type(witness_target) is not DedicatedHostTarget or witness_target.role != "witness":
        _fail("PREFLIGHT_RUNTIME_TRANSPORT_WITNESS_TARGET_REQUIRED")
    try:
        _ssh._target_facts(witness_target)  # type: ignore[attr-defined]
        witness_evidence_config = _witness_runtime.load_root_owned_witness_evidence_delivery_config(
            config=_witness_runtime.RootOwnedWitnessEvidenceVerifierRuntimeConfig(enabled=True)
        )
    except Exception:
        _fail("PREFLIGHT_RUNTIME_TRANSPORT_WITNESS_EVIDENCE_PROVISION_REQUIRED")
    provider_readback = _ecc.RootOwnedArvanEccProviderReadback(
        config=_ecc.RootOwnedArvanEccProviderReadbackConfig(enabled=True),
        runner=RootOwnedArvanEccHttpsGetRunner(),
    )
    ssh_delivery = _ssh.PinnedSshReadonlyAgentDelivery(
        config=_ssh.PinnedSshReadonlyDeliveryConfig(enabled=True),
        runner=RootOwnedPinnedSshProcessRunner(),
    )
    witness_evidence_delivery = _witness_ssh.PinnedSshWitnessEvidenceAgentDelivery(
        config=witness_evidence_config,
        witness_target=witness_target,
        runner=RootOwnedWitnessEvidenceSshProcessRunner(),
    )
    return RootOwnedDedicatedHostPreflightRuntimeAdapters(
        provider_readback=provider_readback,
        agent_delivery=_RoleBoundAgentDelivery(
            ssh_delivery=ssh_delivery,
            ir_witness_evidence_delivery=witness_evidence_delivery,
        ),
    )
