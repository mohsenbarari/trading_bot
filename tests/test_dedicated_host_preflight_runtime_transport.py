"""Tests for concrete but default-off dedicated-host transport runners.

All HTTP and process activity is injected.  These tests open no socket and
start no child process.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core import dedicated_host_preflight_arvan_ecc_readback as ecc
from core import dedicated_host_preflight_pinned_ssh_delivery as ssh
from core import dedicated_host_preflight_runtime_transport as runtime
from core.dedicated_host_preflight_controller import (
    DELIVERY_CONTRACT_BY_ROLE,
    DedicatedHostTarget,
)
from core.dedicated_host_preflight_receipt import (
    PREFLIGHT_RECEIPT_SCHEMA,
    canonical_json_bytes,
)
from scripts.dedicated_host_preflight_manifest import EXPECTED_HOSTS, READONLY_REQUEST_SCHEMA


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "dedicated_host_preflight_runtime_transport.py"
)

_CAMPAIGN_ID = "dedicated-preflight-20260731"
_OPERATION_ID = "e85a1b86-7d55-4d32-8a27-15a21700394f"
_RELEASE_SHA = "a" * 40
_MANIFEST_SHA256 = "b" * 64
_API_KEY = "QzE2MzU1YmQ4Y2YwYjgxYjJlZTI0YjQ1Njc4OTAxMjM0NTY3ODkw"


def _public_key_blob(role: str) -> bytes:
    algorithm = b"ssh-ed25519"
    public = hashlib.sha256(("runtime-test-key:" + role).encode("ascii")).digest()
    return (
        len(algorithm).to_bytes(4, "big")
        + algorithm
        + len(public).to_bytes(4, "big")
        + public
    )


def _known_host_line(role: str) -> str:
    return " ".join(
        (
            EXPECTED_HOSTS[role]["public_ip"],
            "ssh-ed25519",
            base64.b64encode(_public_key_blob(role)).decode("ascii"),
        )
    )


def _target(role: str, *, host_key_sha256: str | None = None) -> DedicatedHostTarget:
    expected = EXPECTED_HOSTS[role]
    route, phase = DELIVERY_CONTRACT_BY_ROLE[role]
    return DedicatedHostTarget(
        role=role,
        instance_id=expected["instance_id"],
        public_ipv4=expected["public_ip"],
        region=expected["region"],
        host_key_sha256=(
            hashlib.sha256(_public_key_blob(role)).hexdigest()
            if host_key_sha256 is None
            else host_key_sha256
        ),
        delivery_route=route,
        delivery_phase=phase,
    )


def _request(role: str) -> bytes:
    return canonical_json_bytes(
        {
            "schema": READONLY_REQUEST_SCHEMA,
            "campaign_id": _CAMPAIGN_ID,
            "operation_id": _OPERATION_ID,
            "release_sha": _RELEASE_SHA,
            "role": role,
            "manifest_sha256": _MANIFEST_SHA256,
        }
    ) + b"\n"


def _receipt(target: DedicatedHostTarget, request: dict[str, str]) -> bytes:
    return canonical_json_bytes(
        {
            "schema": PREFLIGHT_RECEIPT_SCHEMA,
            "status": "observed",
            "observation_mode": "read-only",
            "campaign_id": request["campaign_id"],
            "operation_id": request["operation_id"],
            "release_sha": request["release_sha"],
            "role": target.role,
            "instance": {
                "provider": "arvan_ecc",
                "server_id": target.instance_id,
                "public_ipv4": target.public_ipv4,
            },
            "manifest_sha256": request["manifest_sha256"],
            "observed_at": "2026-07-31T00:00:00Z",
            "observation": {
                "role_marker": target.role,
                "release": {
                    "state": "present",
                    "release_sha": request["release_sha"],
                    "clean": True,
                },
                "runtime": {
                    "docker_state": "active",
                    "container_count": 0,
                    "matrix_process_count": 0,
                    "current_link_present": False,
                },
                "staging_mount": {
                    "present": True,
                    "filesystem": "ext4",
                    "available_bytes": 52_000_000_000,
                    "options": ["nodev", "noexec", "nosuid", "rw"],
                },
            },
        }
    ) + b"\n"


class _CaptureSshRunner:
    def __init__(self, target: DedicatedHostTarget) -> None:
        self.target = target
        self.calls: list[ssh.PinnedSshReadonlyInvocation] = []

    async def run(
        self, *, invocation: ssh.PinnedSshReadonlyInvocation
    ) -> ssh.PinnedSshReadonlyRunnerResult:
        self.calls.append(invocation)
        return ssh.PinnedSshReadonlyRunnerResult(
            exit_code=0,
            stdout_bytes=_receipt(self.target, json.loads(invocation.stdin_bytes)),
        )


class _Reader:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0

    async def read(self, amount: int = -1) -> bytes:
        if self._offset >= len(self._payload):
            return b""
        if amount < 0:
            amount = len(self._payload)
        chunk = self._payload[self._offset : self._offset + amount]
        self._offset += len(chunk)
        return chunk


class _Stdin:
    def __init__(self) -> None:
        self.payload = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.payload.extend(data)

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class _Process:
    def __init__(self, *, stdout: bytes, stderr: bytes = b"", exit_code: int = 0) -> None:
        self.stdin = _Stdin()
        self.stdout = _Reader(stdout)
        self.stderr = _Reader(stderr)
        self._exit_code = exit_code
        self.killed = False

    async def wait(self) -> int:
        return self._exit_code

    def kill(self) -> None:
        self.killed = True


class _ProcessLauncher:
    def __init__(self, process: _Process) -> None:
        self.process = process
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def __call__(self, *arguments: object, **kwargs: object) -> _Process:
        self.calls.append((arguments, dict(kwargs)))
        return self.process


class _HttpResponse:
    def __init__(self, *, status: int, body: bytes) -> None:
        self.status = status
        self._reader = _Reader(body)
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        if self._reader._offset >= len(self._reader._payload):
            return b""
        if amount < 0:
            amount = len(self._reader._payload)
        chunk = self._reader._payload[
            self._reader._offset : self._reader._offset + amount
        ]
        self._reader._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class _HttpsConnection:
    def __init__(self, response: _HttpResponse | Exception) -> None:
        self.response = response
        self.requests: list[tuple[str, str, object, dict[str, str]]] = []
        self.closed = False

    def request(
        self,
        method: str,
        url: str,
        body: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.requests.append((method, url, body, {} if headers is None else dict(headers)))

    def getresponse(self) -> _HttpResponse:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close(self) -> None:
        self.closed = True


class _HttpsFactory:
    def __init__(self, connection: _HttpsConnection) -> None:
        self.connection = connection
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> _HttpsConnection:
        self.calls.append(dict(kwargs))
        return self.connection


class _FakeDelivery:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.calls: list[str] = []

    async def collect_readonly_receipt(self, **kwargs: object) -> dict[str, str]:
        target = kwargs["target"]
        assert isinstance(target, DedicatedHostTarget)
        self.calls.append(target.role)
        return {"marker": self.marker}


@unittest.skipUnless(os.geteuid() == 0, "root-only runtime contract")
class DedicatedHostPreflightRuntimeTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="preflight-runtime-")
        self.root = Path(self.temporary.name)
        self.security_root = self.root / "security"
        self.security_root.mkdir(mode=0o700)
        self.known_hosts = self.security_root / "known_hosts"
        self.identity_file = self.security_root / "identity_ed25519"
        self.identity_file.write_bytes(b"runtime-test-private-key")
        self.identity_file.chmod(0o600)
        self.ssh_binary = self.root / "ssh"
        self.ssh_binary.write_bytes(b"runtime-test-ssh")
        self.ssh_binary.chmod(0o755)
        self.known_hosts.write_text(
            "\n".join(_known_host_line(role) for role in ("bot_fi", "webapp_fi", "witness"))
            + "\n",
            encoding="ascii",
        )
        self.known_hosts.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _fixed_ssh_paths(self):
        return mock.patch.multiple(
            ssh,
            FIXED_PINNED_SSH_BINARY=self.ssh_binary,
            FIXED_DEDICATED_HOST_PREFLIGHT_KNOWN_HOSTS=self.known_hosts,
            FIXED_DEDICATED_HOST_PREFLIGHT_IDENTITY_FILE=self.identity_file,
        )

    def _captured_invocation(self, role: str = "bot_fi") -> ssh.PinnedSshReadonlyInvocation:
        target = _target(role)
        captured = _CaptureSshRunner(target)
        adapter = ssh.PinnedSshReadonlyAgentDelivery(
            config=ssh.PinnedSshReadonlyDeliveryConfig(enabled=True),
            runner=captured,
        )
        request = _request(role)
        with self._fixed_ssh_paths():
            asyncio.run(
                adapter.collect_readonly_receipt(
                    target=target,
                    request_bytes=request,
                    request_sha256=hashlib.sha256(request).hexdigest(),
                    receipt_path=f"dedicated-host-preflight/{role}/receipt.json",
                )
            )
        self.assertEqual(len(captured.calls), 1)
        return captured.calls[0]

    def test_https_runner_is_fixed_tls_direct_get_and_never_follows_redirects(self) -> None:
        target = _target("bot_fi")
        body = b'{"id":"safe-test-body"}'
        response = _HttpResponse(status=200, body=body)
        connection = _HttpsConnection(response)
        factory = _HttpsFactory(connection)
        runner = runtime.RootOwnedArvanEccHttpsGetRunner(connection_factory=factory)
        invocation = ecc.ArvanEccGetServerInvocation(
            endpoint=ecc.FIXED_ARVAN_ECC_ENDPOINT,
            method="GET",
            path=f"/regions/{target.region}/servers/{target.instance_id}",
            authorization_scheme="Apikey",
            api_key=_API_KEY,
        )

        result = asyncio.run(runner.run(invocation=invocation))

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.body, body)
        self.assertEqual(len(factory.calls), 1)
        call = factory.calls[0]
        self.assertEqual(call["host"], "napi.arvancloud.ir")
        self.assertEqual(call["port"], 443)
        self.assertEqual(call["timeout"], 5)
        context = call["context"]
        self.assertTrue(getattr(context, "check_hostname"))
        self.assertGreaterEqual(
            getattr(context, "minimum_version"),
            __import__("ssl").TLSVersion.TLSv1_2,
        )
        self.assertEqual(
            connection.requests,
            [
                (
                    "GET",
                    f"/ecc/v1/regions/{target.region}/servers/{target.instance_id}",
                    None,
                    {"Accept": "application/json", "Authorization": "Apikey " + _API_KEY},
                )
            ],
        )
        self.assertTrue(connection.closed)
        self.assertTrue(response.closed)

    def test_https_runner_rejects_foreign_endpoint_and_oversize_without_calling_transport(self) -> None:
        target = _target("bot_fi")
        connection = _HttpsConnection(_HttpResponse(status=200, body=b"{}"))
        factory = _HttpsFactory(connection)
        runner = runtime.RootOwnedArvanEccHttpsGetRunner(connection_factory=factory)
        foreign = ecc.ArvanEccGetServerInvocation(
            endpoint=ecc.FIXED_ARVAN_ECC_ENDPOINT,
            method="GET",
            path="/regions/foreign/servers/00000000-0000-0000-0000-000000000001",
            authorization_scheme="Apikey",
            api_key=_API_KEY,
        )
        with self.assertRaisesRegex(
            runtime.DedicatedHostPreflightRuntimeTransportError,
            "^ARVAN_ECC_HTTPS_INVOCATION_INVALID$",
        ):
            asyncio.run(runner.run(invocation=foreign))
        self.assertEqual(factory.calls, [])

        oversized = _HttpsConnection(
            _HttpResponse(status=200, body=b"x" * (64 * 1024 + 1))
        )
        oversized_runner = runtime.RootOwnedArvanEccHttpsGetRunner(
            connection_factory=_HttpsFactory(oversized)
        )
        good = ecc.ArvanEccGetServerInvocation(
            endpoint=ecc.FIXED_ARVAN_ECC_ENDPOINT,
            method="GET",
            path=f"/regions/{target.region}/servers/{target.instance_id}",
            authorization_scheme="Apikey",
            api_key=_API_KEY,
        )
        with self.assertRaisesRegex(
            runtime.DedicatedHostPreflightRuntimeTransportError,
            "^ARVAN_ECC_HTTPS_RESPONSE_OVERSIZE$",
        ):
            asyncio.run(oversized_runner.run(invocation=good))

    def test_https_runner_collapses_transport_error_and_never_leaks_key(self) -> None:
        target = _target("webapp_fi")
        runner = runtime.RootOwnedArvanEccHttpsGetRunner(
            connection_factory=_HttpsFactory(
                _HttpsConnection(RuntimeError("token=never-leak"))
            )
        )
        invocation = ecc.ArvanEccGetServerInvocation(
            endpoint=ecc.FIXED_ARVAN_ECC_ENDPOINT,
            method="GET",
            path=f"/regions/{target.region}/servers/{target.instance_id}",
            authorization_scheme="Apikey",
            api_key=_API_KEY,
        )
        with self.assertRaises(runtime.DedicatedHostPreflightRuntimeTransportError) as raised:
            asyncio.run(runner.run(invocation=invocation))
        self.assertEqual(raised.exception.code, "ARVAN_ECC_HTTPS_REQUEST_FAILED")
        self.assertNotIn("never-leak", str(raised.exception))
        self.assertNotIn(_API_KEY, str(raised.exception))

    def test_pinned_ssh_runner_uses_only_exact_exec_argv_clean_environment_and_bounded_output(self) -> None:
        invocation = self._captured_invocation()
        process = _Process(stdout=b"receipt-bytes", stderr=b"diagnostic", exit_code=0)
        launcher = _ProcessLauncher(process)
        runner = runtime.RootOwnedPinnedSshProcessRunner(process_launcher=launcher)

        with self._fixed_ssh_paths():
            result = asyncio.run(runner.run(invocation=invocation))

        self.assertEqual(result, ssh.PinnedSshReadonlyRunnerResult(0, b"receipt-bytes"))
        self.assertEqual(process.stdin.payload, invocation.stdin_bytes)
        self.assertTrue(process.stdin.closed)
        self.assertEqual(len(launcher.calls), 1)
        arguments, kwargs = launcher.calls[0]
        self.assertEqual(arguments, invocation.arguments)
        self.assertEqual(kwargs["stdin"], asyncio.subprocess.PIPE)
        self.assertEqual(kwargs["stdout"], asyncio.subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], asyncio.subprocess.PIPE)
        self.assertEqual(kwargs["env"], dict(runtime._FIXED_SSH_EXEC_ENV))
        self.assertTrue(kwargs["close_fds"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertNotIn("shell", kwargs)

    def test_pinned_ssh_runner_refuses_webapp_ir_and_oversize_before_or_without_receipt_release(self) -> None:
        invocation = self._captured_invocation()
        launcher = _ProcessLauncher(_Process(stdout=b"ignored"))
        runner = runtime.RootOwnedPinnedSshProcessRunner(process_launcher=launcher)
        with self.assertRaisesRegex(
            runtime.DedicatedHostPreflightRuntimeTransportError,
            "^PINNED_SSH_WEBAPP_IR_OBJECT_STORAGE_PULL_REQUIRED$",
        ):
            asyncio.run(runner.run(invocation=replace(invocation, role="webapp_ir")))
        self.assertEqual(launcher.calls, [])

        oversized_process = _Process(stdout=b"x" * (32 * 1024 + 1))
        oversized_launcher = _ProcessLauncher(oversized_process)
        oversized_runner = runtime.RootOwnedPinnedSshProcessRunner(
            process_launcher=oversized_launcher
        )
        with self._fixed_ssh_paths():
            with self.assertRaisesRegex(
                runtime.DedicatedHostPreflightRuntimeTransportError,
                "^PINNED_SSH_RUNTIME_OUTPUT_OVERSIZE$",
            ):
                asyncio.run(oversized_runner.run(invocation=invocation))
        self.assertTrue(oversized_process.killed)

    def test_pinned_ssh_nonzero_exit_never_releases_stdout_or_stderr(self) -> None:
        invocation = self._captured_invocation()
        process = _Process(
            stdout=b"https://diagnostic.invalid/?token=should-not-return",
            stderr=b"secret=should-not-return",
            exit_code=255,
        )
        runner = runtime.RootOwnedPinnedSshProcessRunner(
            process_launcher=_ProcessLauncher(process)
        )
        with self._fixed_ssh_paths():
            result = asyncio.run(runner.run(invocation=invocation))
        self.assertEqual(result.exit_code, 255)
        self.assertEqual(result.stdout_bytes, b"")

    def test_runtime_config_and_dispatcher_require_witness_evidence_before_any_fi_transport(self) -> None:
        raw_config = {
            "schema": runtime.DEDICATED_HOST_PREFLIGHT_RUNTIME_TRANSPORT_CONFIG_SCHEMA,
            "enabled": True,
            "mode": "read-only",
            "provider_transport": "fixed-https-get-only",
            "fi_receipt_transport": "pinned-ssh-readonly-agent",
            "ir_receipt_transport": "pinned-ssh-witness-evidence-agent",
            "direct_finland_to_iran": "forbidden",
        }
        config = runtime.parse_root_owned_dedicated_host_preflight_runtime_transport_config(
            raw_config
        )
        with mock.patch.object(
            runtime._witness_runtime,  # type: ignore[attr-defined]
            "load_root_owned_witness_evidence_delivery_config",
            side_effect=RuntimeError("local fixture failure"),
        ), mock.patch.object(runtime, "RootOwnedArvanEccHttpsGetRunner") as ecc_runner, mock.patch.object(
            runtime, "RootOwnedPinnedSshProcessRunner"
        ) as ssh_runner:
            with self.assertRaisesRegex(
                runtime.DedicatedHostPreflightRuntimeTransportError,
                "^PREFLIGHT_RUNTIME_TRANSPORT_WITNESS_EVIDENCE_PROVISION_REQUIRED$",
            ):
                runtime.assemble_root_owned_dedicated_host_preflight_runtime_adapters(
                    config=config,
                    witness_target=_target("witness"),
                )
        self.assertFalse(ecc_runner.called)
        self.assertFalse(ssh_runner.called)

        # The assembler cannot be reached with an omitted/foreign Witness
        # target, so a generic delivery object cannot bypass the root-pinned
        # central verifier or source-pinned Witness transport identity.
        with self.assertRaisesRegex(
            runtime.DedicatedHostPreflightRuntimeTransportError,
            "^PREFLIGHT_RUNTIME_TRANSPORT_WITNESS_TARGET_REQUIRED$",
        ):
            runtime.assemble_root_owned_dedicated_host_preflight_runtime_adapters(
                config=config,
                witness_target=None,
            )

        provisioned = object.__new__(
            runtime._witness_ssh.PinnedSshWitnessEvidenceDeliveryConfig  # type: ignore[attr-defined]
        )
        with mock.patch.object(
            runtime._witness_runtime,  # type: ignore[attr-defined]
            "load_root_owned_witness_evidence_delivery_config",
            return_value=provisioned,
        ) as provision:
            adapters = runtime.assemble_root_owned_dedicated_host_preflight_runtime_adapters(
                config=config,
                witness_target=_target("witness"),
            )
        provision.assert_called_once()
        self.assertIsInstance(adapters.agent_delivery, runtime._RoleBoundAgentDelivery)

        ssh_delivery = _FakeDelivery("ssh")
        ir_delivery = _FakeDelivery("witness-evidence")
        dispatcher = runtime._RoleBoundAgentDelivery(  # type: ignore[attr-defined]
            ssh_delivery=ssh_delivery,  # type: ignore[arg-type]
            ir_witness_evidence_delivery=ir_delivery,  # type: ignore[arg-type]
        )
        ir_target = _target("webapp_ir", host_key_sha256="c" * 64)
        result = asyncio.run(
            dispatcher.collect_readonly_receipt(
                target=ir_target,
                request_bytes=_request("webapp_ir"),
                request_sha256="d" * 64,
                receipt_path="dedicated-host-preflight/webapp_ir/receipt.json",
            )
        )
        self.assertEqual(result, {"marker": "witness-evidence"})
        self.assertEqual(ir_delivery.calls, ["webapp_ir"])
        self.assertEqual(ssh_delivery.calls, [])

    def test_module_uses_no_redirect_or_proxy_client_and_all_live_seams_are_injected(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("requests", source)
        self.assertNotIn("urlopen", source)
        self.assertNotIn("ProxyHandler", source)
        self.assertIn("create_subprocess_exec", source)
        self.assertIn("connection_factory", source)
        self.assertIn("process_launcher", source)
        self.assertNotIn("shell=True", source)


if __name__ == "__main__":
    unittest.main()
