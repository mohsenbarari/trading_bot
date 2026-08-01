"""Tests for the fail-closed, injected pinned-SSH preflight adapter.

The runner double records an immutable argv-like invocation.  It never opens a
socket or starts a process, so these tests exercise only the local policy
boundary.
"""

from __future__ import annotations

import asyncio
import ast
import base64
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core import dedicated_host_preflight_pinned_ssh_delivery as delivery_module
from core.dedicated_host_preflight_controller import (
    AGENT_DELIVERY_RESPONSE_SCHEMA,
    DELIVERY_CONTRACT_BY_ROLE,
    RECEIPT_PATH_BY_ROLE,
    DedicatedHostTarget,
)
from core.dedicated_host_preflight_receipt import (
    PREFLIGHT_RECEIPT_SCHEMA,
    canonical_json_bytes,
)
from scripts.dedicated_host_preflight_manifest import (
    EXPECTED_HOSTS,
    READONLY_REQUEST_SCHEMA,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "dedicated_host_preflight_pinned_ssh_delivery.py"
)

_SSH_ROLES = ("bot_fi", "webapp_fi", "witness")
_CAMPAIGN_ID = "dedicated-preflight-20260731"
_OPERATION_ID = "e85a1b86-7d55-4d32-8a27-15a21700394f"
_RELEASE_SHA = "a" * 40
_MANIFEST_SHA256 = "b" * 64


def _public_key_blob(role: str) -> bytes:
    """Return a syntactically typed, non-production SSH public-key blob."""

    algorithm = b"ssh-ed25519"
    public = hashlib.sha256(("test-key:" + role).encode("ascii")).digest()
    return (
        len(algorithm).to_bytes(4, "big")
        + algorithm
        + len(public).to_bytes(4, "big")
        + public
    )


def _known_host_line(role: str, *, blob: bytes | None = None) -> str:
    key = _public_key_blob(role) if blob is None else blob
    return " ".join(
        (
            EXPECTED_HOSTS[role]["public_ip"],
            "ssh-ed25519",
            base64.b64encode(key).decode("ascii"),
        )
    )


def _target(role: str) -> DedicatedHostTarget:
    expected = EXPECTED_HOSTS[role]
    route, phase = DELIVERY_CONTRACT_BY_ROLE[role]
    return DedicatedHostTarget(
        role=role,
        instance_id=expected["instance_id"],
        public_ipv4=expected["public_ip"],
        region=expected["region"],
        host_key_sha256=hashlib.sha256(_public_key_blob(role)).hexdigest(),
        delivery_route=route,
        delivery_phase=phase,
    )


def _request(role: str, **changes: object) -> bytes:
    value: dict[str, object] = {
        "schema": READONLY_REQUEST_SCHEMA,
        "campaign_id": _CAMPAIGN_ID,
        "operation_id": _OPERATION_ID,
        "release_sha": _RELEASE_SHA,
        "role": role,
        "manifest_sha256": _MANIFEST_SHA256,
    }
    value.update(changes)
    return canonical_json_bytes(value) + b"\n"


def _receipt_for(target: DedicatedHostTarget, request: dict[str, str]) -> bytes:
    value: dict[str, object] = {
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
    return canonical_json_bytes(value) + b"\n"


class RecordingRunner:
    """Pure async double; it returns only explicit test receipt bytes."""

    def __init__(self, targets: dict[str, DedicatedHostTarget]) -> None:
        self.targets = targets
        self.calls: list[delivery_module.PinnedSshReadonlyInvocation] = []
        self.result_override: delivery_module.PinnedSshReadonlyRunnerResult | None = None
        self.exception: Exception | None = None

    async def run(
        self,
        *,
        invocation: delivery_module.PinnedSshReadonlyInvocation,
    ) -> delivery_module.PinnedSshReadonlyRunnerResult:
        self.calls.append(invocation)
        if self.exception is not None:
            raise self.exception
        if self.result_override is not None:
            return self.result_override
        request = json.loads(invocation.stdin_bytes)
        return delivery_module.PinnedSshReadonlyRunnerResult(
            exit_code=0,
            stdout_bytes=_receipt_for(self.targets[invocation.role], request),
        )


class DedicatedHostPreflightPinnedSshDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        if os.geteuid() != 0:
            self.skipTest("root-owned secure-file boundary requires a root test runtime")
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.security_root = self.root / "security"
        self.security_root.mkdir(mode=0o700)
        self.known_hosts = self.security_root / "known_hosts"
        self.identity_file = self.security_root / "identity_ed25519"
        self.identity_file.write_bytes(b"test-only-private-key-material")
        self.identity_file.chmod(0o600)
        self.ssh_binary = self.root / "ssh"
        self.ssh_binary.write_bytes(b"test-only-ssh-binary")
        self.ssh_binary.chmod(0o755)
        self.targets = {role: _target(role) for role in (*_SSH_ROLES, "webapp_ir")}
        self.runner = RecordingRunner(self.targets)
        self._write_known_hosts()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_known_hosts(self, *, bot_blob: bytes | None = None) -> None:
        self.known_hosts.write_text(
            "\n".join(
                (
                    _known_host_line("bot_fi", blob=bot_blob),
                    _known_host_line("webapp_fi"),
                    _known_host_line("witness"),
                )
            )
            + "\n",
            encoding="ascii",
        )
        self.known_hosts.chmod(0o600)

    def _delivery(
        self,
        *,
        config: delivery_module.PinnedSshReadonlyDeliveryConfig | None = None,
        runner: RecordingRunner | None = None,
    ) -> delivery_module.PinnedSshReadonlyAgentDelivery:
        return delivery_module.PinnedSshReadonlyAgentDelivery(
            config=(
                delivery_module.PinnedSshReadonlyDeliveryConfig(enabled=True)
                if config is None
                else config
            ),
            runner=self.runner if runner is None else runner,
        )

    def _call(
        self,
        adapter: delivery_module.PinnedSshReadonlyAgentDelivery,
        target: DedicatedHostTarget,
        request: bytes | None = None,
        receipt_path: str | None = None,
    ) -> dict[str, object]:
        raw_request = _request(target.role) if request is None else request
        result = asyncio.run(
            adapter.collect_readonly_receipt(
                target=target,
                request_bytes=raw_request,
                request_sha256=hashlib.sha256(raw_request).hexdigest(),
                receipt_path=(
                    RECEIPT_PATH_BY_ROLE[target.role]
                    if receipt_path is None
                    else receipt_path
                ),
            )
        )
        return dict(result)

    def _fixed_paths(self):
        return mock.patch.multiple(
            delivery_module,
            FIXED_PINNED_SSH_BINARY=self.ssh_binary,
            FIXED_DEDICATED_HOST_PREFLIGHT_KNOWN_HOSTS=self.known_hosts,
            FIXED_DEDICATED_HOST_PREFLIGHT_IDENTITY_FILE=self.identity_file,
        )

    def test_permitted_roles_make_only_the_fixed_argv_and_controller_response(self) -> None:
        for role in _SSH_ROLES:
            with self.subTest(role=role), self._fixed_paths():
                result = self._call(self._delivery(), self.targets[role])

            invocation = self.runner.calls[-1]
            self.assertEqual(invocation.ssh_binary, self.ssh_binary)
            self.assertEqual(invocation.known_hosts, self.known_hosts)
            self.assertEqual(invocation.identity_file, self.identity_file)
            self.assertEqual(invocation.role, role)
            self.assertEqual(invocation.host_key_sha256, self.targets[role].host_key_sha256)
            self.assertEqual(invocation.environment, ())
            self.assertEqual(
                invocation.arguments,
                (
                    str(self.ssh_binary),
                    "-F", "/dev/null",
                    "-i", str(self.identity_file),
                    "-o", "BatchMode=yes",
                    "-o", "PasswordAuthentication=no",
                    "-o", "KbdInteractiveAuthentication=no",
                    "-o", "ChallengeResponseAuthentication=no",
                    "-o", "PubkeyAuthentication=yes",
                    "-o", "IdentitiesOnly=yes",
                    "-o", "IdentityAgent=none",
                    "-o", "StrictHostKeyChecking=yes",
                    "-o", "UserKnownHostsFile=" + str(self.known_hosts),
                    "-o", "GlobalKnownHostsFile=/dev/null",
                    "-o", "UpdateHostKeys=no",
                    "-o", "HashKnownHosts=no",
                    "-o", "ForwardAgent=no",
                    "-o", "ClearAllForwardings=yes",
                    "-o", "PermitLocalCommand=no",
                    "-o", "RequestTTY=no",
                    "-o", "ConnectTimeout=5",
                    "-o", "ConnectionAttempts=1",
                    "-p", "22",
                    "preflight@" + self.targets[role].public_ipv4,
                    "collect-readonly-receipt",
                ),
            )
            self.assertEqual(
                set(result),
                {
                    "schema",
                    "role",
                    "delivery_route",
                    "delivery_phase",
                    "host_key_sha256",
                    "request_sha256",
                    "receipt_path",
                    "receipt_sha256",
                    "receipt_bytes",
                },
            )
            self.assertEqual(result["schema"], AGENT_DELIVERY_RESPONSE_SCHEMA)
            self.assertEqual(result["delivery_route"], "pinned-ssh-readonly-agent")
            self.assertEqual(result["delivery_phase"], "collect-readonly-receipt")
            self.assertNotIn("arguments", result)
            self.assertNotIn("ssh_binary", result)

    def test_webapp_ir_and_all_caller_controlled_route_inputs_fail_before_runner(self) -> None:
        adapter = self._delivery()
        injection = "collect-readonly-receipt;echo-should-not-run"
        cases = (
            (
                self.targets["webapp_ir"],
                _request("webapp_ir"),
                RECEIPT_PATH_BY_ROLE["webapp_ir"],
                "PINNED_SSH_WEBAPP_IR_OBJECT_STORAGE_PULL_REQUIRED",
            ),
            (
                replace(self.targets["bot_fi"], public_ipv4="8.8.8.8"),
                _request("bot_fi"),
                RECEIPT_PATH_BY_ROLE["bot_fi"],
                "PINNED_SSH_TARGET_SOURCE_PIN_MISMATCH",
            ),
            (
                self.targets["bot_fi"],
                _request("bot_fi", command=injection),
                RECEIPT_PATH_BY_ROLE["bot_fi"],
                "PINNED_SSH_REQUEST_INVALID",
            ),
            (
                self.targets["bot_fi"],
                _request("bot_fi"),
                "/tmp/" + injection,
                "PINNED_SSH_RECEIPT_PATH_INVALID",
            ),
        )
        for target, raw_request, receipt_path, code in cases:
            with self.subTest(code=code), self._fixed_paths():
                with self.assertRaisesRegex(
                    delivery_module.DedicatedHostPreflightPinnedSshDeliveryError,
                    "^" + code + "$",
                ) as raised:
                    self._call(adapter, target, raw_request, receipt_path)
            self.assertNotIn(injection, str(raised.exception))
        self.assertEqual(self.runner.calls, [])

    def test_known_hosts_and_binary_must_be_private_root_controlled_and_exactly_pinned(
        self,
    ) -> None:
        adapter = self._delivery()
        self._write_known_hosts(bot_blob=_public_key_blob("witness"))
        with self._fixed_paths():
            with self.assertRaisesRegex(
                delivery_module.DedicatedHostPreflightPinnedSshDeliveryError,
                "^PINNED_SSH_HOST_KEY_PIN_MISMATCH$",
            ):
                self._call(adapter, self.targets["bot_fi"])
        self.assertEqual(self.runner.calls, [])

        self._write_known_hosts()
        self.known_hosts.chmod(0o644)
        with self._fixed_paths():
            with self.assertRaisesRegex(
                delivery_module.DedicatedHostPreflightPinnedSshDeliveryError,
                "^PINNED_SSH_KNOWN_HOSTS_INVALID$",
            ):
                self._call(adapter, self.targets["bot_fi"])
        self.assertEqual(self.runner.calls, [])

        self.known_hosts.chmod(0o600)
        self.ssh_binary.chmod(0o777)
        with self._fixed_paths():
            with self.assertRaisesRegex(
                delivery_module.DedicatedHostPreflightPinnedSshDeliveryError,
                "^PINNED_SSH_BINARY_UNAVAILABLE$",
            ):
                self._call(adapter, self.targets["bot_fi"])
        self.assertEqual(self.runner.calls, [])

        self.ssh_binary.chmod(0o755)
        self.identity_file.chmod(0o644)
        with self._fixed_paths():
            with self.assertRaisesRegex(
                delivery_module.DedicatedHostPreflightPinnedSshDeliveryError,
                "^PINNED_SSH_IDENTITY_FILE_INVALID$",
            ):
                self._call(adapter, self.targets["bot_fi"])
        self.assertEqual(self.runner.calls, [])

    def test_disabled_nonroot_runner_and_receipt_errors_are_fail_closed_and_redacted(self) -> None:
        raw_request = _request("bot_fi")
        disabled = delivery_module.PinnedSshReadonlyAgentDelivery(runner=self.runner)
        with self.assertRaisesRegex(
            delivery_module.DedicatedHostPreflightPinnedSshDeliveryError,
            "^PINNED_SSH_DISABLED$",
        ):
            self._call(disabled, self.targets["bot_fi"], raw_request)
        self.assertEqual(self.runner.calls, [])

        with self._fixed_paths(), mock.patch.object(
            delivery_module.os, "geteuid", return_value=1000
        ):
            with self.assertRaisesRegex(
                delivery_module.DedicatedHostPreflightPinnedSshDeliveryError,
                "^PINNED_SSH_ROOT_RUNTIME_REQUIRED$",
            ):
                self._call(self._delivery(), self.targets["bot_fi"], raw_request)
        self.assertEqual(self.runner.calls, [])

        secret_shaped_stdout = b"https://invalid.example/?token=not-a-receipt"
        self.runner.result_override = delivery_module.PinnedSshReadonlyRunnerResult(
            exit_code=1,
            stdout_bytes=secret_shaped_stdout,
        )
        with self._fixed_paths():
            with self.assertRaisesRegex(
                delivery_module.DedicatedHostPreflightPinnedSshDeliveryError,
                "^PINNED_SSH_RUNNER_FAILED$",
            ) as raised:
                self._call(self._delivery(), self.targets["bot_fi"], raw_request)
        self.assertNotIn("invalid.example", str(raised.exception))
        self.assertNotIn("token", str(raised.exception))

        self.runner.result_override = delivery_module.PinnedSshReadonlyRunnerResult(
            exit_code=0,
            stdout_bytes=b"{}\n",
        )
        with self._fixed_paths():
            with self.assertRaisesRegex(
                delivery_module.DedicatedHostPreflightPinnedSshDeliveryError,
                "^PINNED_SSH_RECEIPT_INVALID$",
            ):
                self._call(self._delivery(), self.targets["bot_fi"], raw_request)

    def test_module_contains_no_live_ssh_network_or_process_implementation(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertTrue(
            {
                "subprocess",
                "socket",
                "paramiko",
                "asyncssh",
                "requests",
                "urllib",
            }.isdisjoint(imports)
        )
        self.assertNotIn("os.system", source)
        self.assertNotIn("Popen", source)
        self.assertNotIn("create_subprocess", source)


if __name__ == "__main__":
    unittest.main()
