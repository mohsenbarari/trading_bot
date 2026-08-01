"""Local adversarial tests for the FI-side SSH receipt-agent boundary.

All collector activity is represented by a fake in-memory runner.  These tests
never open SSH, create an account, modify sudoers/sshd, or reach WA-IR.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import unittest

from core import dedicated_host_preflight_receipt_agent_boundary as boundary
from core.dedicated_host_preflight_receipt import (
    PREFLIGHT_RECEIPT_SCHEMA,
    canonical_json_bytes,
    parse_preflight_receipt,
)
from scripts.dedicated_host_preflight_manifest import EXPECTED_HOSTS, READONLY_REQUEST_SCHEMA


CAMPAIGN_ID = "dedicated-preflight-20260731"
OPERATION_ID = "e85a1b86-7d55-4d32-8a27-15a21700394f"
AGENT_RELEASE_SHA = "a" * 40
RELEASE_SHA = "b" * 40
MANIFEST_SHA256 = "c" * 64
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "dedicated_host_preflight_receipt_agent_boundary.py"
)
DISPATCHER_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "run_dedicated_host_preflight_receipt_dispatcher.py"
)
ROOT_COLLECTOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "run_dedicated_host_preflight_root_collector.py"
)
WITNESS_EVIDENCE_DISPATCHER_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "run_dedicated_host_preflight_witness_evidence_dispatcher.py"
)
WITNESS_EVIDENCE_ROOT_COLLECTOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "run_dedicated_host_preflight_witness_evidence_root_collector.py"
)


def _public_key() -> str:
    algorithm = b"ssh-ed25519"
    public = hashlib.sha256(b"preflight-controller-public-key").digest()
    wire = (
        len(algorithm).to_bytes(4, "big")
        + algorithm
        + len(public).to_bytes(4, "big")
        + public
    )
    return "ssh-ed25519 " + base64.b64encode(wire).decode("ascii")


def _config(**changes: object) -> boundary.ReceiptAgentInstallationConfig:
    values: dict[str, object] = {
        "enabled": False,
        "site_role": "bot_fi",
        "agent_release_sha": AGENT_RELEASE_SHA,
        "controller_public_key": _public_key(),
    }
    values.update(changes)
    return boundary.ReceiptAgentInstallationConfig(**values)


def _request(role: str = "bot_fi") -> bytes:
    return canonical_json_bytes(
        {
            "schema": READONLY_REQUEST_SCHEMA,
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "role": role,
            "manifest_sha256": MANIFEST_SHA256,
        }
    ) + b"\n"


def _receipt(role: str = "bot_fi") -> bytes:
    host = EXPECTED_HOSTS[role]
    return canonical_json_bytes(
        {
            "schema": PREFLIGHT_RECEIPT_SCHEMA,
            "status": "observed",
            "observation_mode": "read-only",
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "role": role,
            "instance": {
                "provider": "arvan_ecc",
                "server_id": host["instance_id"],
                "public_ipv4": host["public_ip"],
            },
            "manifest_sha256": MANIFEST_SHA256,
            "observed_at": "2026-07-31T00:00:00Z",
            "observation": {
                "role_marker": role,
                "release": {
                    "state": "present",
                    "release_sha": RELEASE_SHA,
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


class _Runner:
    def __init__(self, result: boundary.ReceiptAgentDispatcherRunnerResult) -> None:
        self.result = result
        self.calls: list[boundary.ReceiptAgentDispatcherInvocation] = []

    def run(
        self, *, invocation: boundary.ReceiptAgentDispatcherInvocation
    ) -> boundary.ReceiptAgentDispatcherRunnerResult:
        self.calls.append(invocation)
        return self.result


class _WitnessEvidenceRunner:
    def __init__(self, result: boundary.WitnessEvidenceAgentDispatcherRunnerResult) -> None:
        self.result = result
        self.calls: list[boundary.WitnessEvidenceAgentDispatcherInvocation] = []

    def run(
        self, *, invocation: boundary.WitnessEvidenceAgentDispatcherInvocation
    ) -> boundary.WitnessEvidenceAgentDispatcherRunnerResult:
        self.calls.append(invocation)
        return self.result


class DedicatedHostPreflightReceiptAgentBoundaryTests(unittest.TestCase):
    def test_default_off_render_has_exact_shell_sshd_key_and_sudo_boundaries(self) -> None:
        rendered = boundary.render_receipt_agent_assets(_config())
        self.assertFalse(rendered.config.enabled)
        self.assertFalse(rendered.installation_authorized)
        self.assertFalse(rendered.execution_authorized)
        self.assertFalse(rendered.promotion_authorized)

        runtime = json.loads(
            rendered.file(boundary.FIXED_PREFLIGHT_ROOT_COLLECTOR_CONFIG)
        )
        self.assertFalse(runtime["enabled"])
        self.assertEqual("bot_fi", runtime["site_role"])
        self.assertEqual("forbidden", runtime["direct_finland_to_iran"])
        self.assertEqual(
            boundary.ReceiptAgentRuntimeConfig(
                enabled=False,
                site_role="bot_fi",
                agent_release_sha=AGENT_RELEASE_SHA,
            ),
            boundary.parse_receipt_agent_runtime_config(runtime),
        )

        sshd = rendered.file(boundary.FIXED_PREFLIGHT_SSHD_CONFIG).decode("ascii")
        self.assertIn("Match User preflight", sshd)
        self.assertIn("AuthenticationMethods publickey", sshd)
        self.assertIn("PermitTTY no", sshd)
        self.assertIn("PermitUserEnvironment no", sshd)
        self.assertIn("AllowTcpForwarding no", sshd)
        self.assertIn("AllowStreamLocalForwarding no", sshd)
        self.assertIn("AllowAgentForwarding no", sshd)
        self.assertIn("X11Forwarding no", sshd)
        self.assertIn("PermitOpen none", sshd)
        self.assertIn("PermitListen none", sshd)
        self.assertIn("DisableForwarding yes", sshd)
        self.assertIn("ForceCommand exec /usr/bin/python3 -I ", sshd)
        self.assertIn("Match all", sshd)
        self.assertNotIn("AllowUsers", sshd)

        authorized = rendered.file(boundary.FIXED_PREFLIGHT_AUTHORIZED_KEYS)
        self.assertEqual(b"restrict " + _public_key().encode("ascii") + b"\n", authorized)
        self.assertNotIn(b"command=", authorized)

        sudoers = rendered.file(boundary.FIXED_PREFLIGHT_SUDOERS).decode("ascii")
        self.assertIn("Defaults:preflight env_reset", sudoers)
        self.assertIn("Defaults:preflight !setenv", sudoers)
        self.assertIn("NOPASSWD:NOSETENV", sudoers)
        self.assertIn(' ""', sudoers)
        self.assertNotIn("SETENV:", sudoers.replace("NOSETENV:", ""))
        self.assertNotIn("*", sudoers)

        force_shell = rendered.file(boundary.FIXED_PREFLIGHT_FORCE_SHELL).decode("ascii")
        self.assertTrue(force_shell.startswith("#!/bin/sh\n"))
        self.assertIn('[ "$#" -eq 2 ]', force_shell)
        self.assertIn('[ "$1" = "-c" ]', force_shell)
        self.assertIn("ForceCommand", sshd)
        self.assertIn("exit 126", force_shell)
        self.assertNotIn("SSH_ORIGINAL_COMMAND", force_shell)

        expected_force_command = sshd.split("ForceCommand ", 1)[1].split("\n", 1)[0]
        accepted = subprocess.run(
            ["/bin/sh", "-c", force_shell, "receipt-force-shell", "-c", expected_force_command],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # The dispatcher path is intentionally not installed in the test, so
        # success reaches the attempted fixed exec (Python returns 2 here),
        # while a wrong client command must take the explicit reject branch.
        self.assertIn(accepted.returncode, {2, 127})
        rejected = subprocess.run(
            ["/bin/sh", "-c", force_shell, "receipt-force-shell", "-c", "id"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertEqual(126, rejected.returncode)

        account = json.loads(rendered.file(boundary.FIXED_PREFLIGHT_ACCOUNT_POLICY))
        self.assertEqual("preflight", account["account"])
        self.assertEqual(str(boundary.FIXED_PREFLIGHT_FORCE_SHELL), account["shell"])
        self.assertEqual("only-exact-sshd-forcecommand-v1", account["shell_policy"])
        self.assertEqual([], account["supplementary_groups"])
        self.assertFalse(account["webapp_ir_supported"])

        for item in rendered.files:
            self.assertTrue(item.destination.is_absolute())
            self.assertIn(item.mode, {0o440, 0o600, 0o644, 0o755})
            self.assertTrue(item.content.endswith(b"\n"))

    def test_config_and_request_reject_wa_ir_key_options_and_noncanonical_input(self) -> None:
        with self.assertRaisesRegex(
            boundary.ReceiptAgentBoundaryError,
            "^PREFLIGHT_RECEIPT_AGENT_WEBAPP_IR_FORBIDDEN$",
        ):
            boundary.render_receipt_agent_assets(_config(site_role="webapp_ir"))

        for bad_key in (
            "ssh-rsa AAAA",
            _public_key() + " comment",
            "command=anything " + _public_key(),
            "ssh-ed25519 AAAA",
        ):
            with self.subTest(bad_key=bad_key[:24]), self.assertRaisesRegex(
                boundary.ReceiptAgentBoundaryError,
                "PREFLIGHT_RECEIPT_AGENT_INSTALLATION_CONFIG_INVALID",
            ):
                boundary.render_receipt_agent_assets(_config(controller_public_key=bad_key))

        for payload in (
            _request("webapp_ir"),
            json.dumps(json.loads(_request())).encode("ascii") + b"\n",
            canonical_json_bytes({**json.loads(_request()), "command": "sh"}) + b"\n",
            b"x" * (4096 + 1),
        ):
            with self.subTest(payload=payload[:32]), self.assertRaises(
                boundary.ReceiptAgentBoundaryError
            ):
                boundary.parse_receipt_agent_request_payload(payload)

    def test_dispatcher_uses_exact_clean_no_argument_sudo_invocation_and_fake_receipt(self) -> None:
        runner = _Runner(
            boundary.ReceiptAgentDispatcherRunnerResult(0, _receipt())
        )
        dispatcher = boundary.ReceiptAgentDispatcher(
            agent_release_sha=AGENT_RELEASE_SHA,
            runner=runner,
        )
        result = dispatcher.dispatch(
            original_command="collect-readonly-receipt",
            arguments=(),
            account_name="preflight",
            account_uid=999,
            request_bytes=_request(),
        )
        self.assertEqual(_receipt(), result)
        self.assertEqual(parse_preflight_receipt(result)["role"], "bot_fi")
        self.assertEqual(1, len(runner.calls))
        invocation = runner.calls[0]
        _dispatcher, root_collector, _readonly = boundary.agent_source_paths(
            AGENT_RELEASE_SHA
        )
        self.assertEqual(
            (
                "/usr/bin/sudo",
                "-n",
                "-u",
                "root",
                "--",
                "/usr/bin/python3",
                "-I",
                str(root_collector),
            ),
            invocation.arguments,
        )
        self.assertEqual(_request(), invocation.stdin_bytes)
        self.assertEqual(
            (
                ("HOME", "/nonexistent"),
                ("LANG", "C"),
                ("LC_ALL", "C"),
                ("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"),
            ),
            invocation.environment,
        )

    def test_dispatcher_rejects_commands_accounts_bad_collector_and_bad_receipt_before_output(self) -> None:
        runner = _Runner(
            boundary.ReceiptAgentDispatcherRunnerResult(0, _receipt())
        )
        dispatcher = boundary.ReceiptAgentDispatcher(
            agent_release_sha=AGENT_RELEASE_SHA,
            runner=runner,
        )
        for changes in (
            {"original_command": "sh -c whoami"},
            {"arguments": ("anything",)},
            {"account_name": "root", "account_uid": 0},
            {"request_bytes": _request("webapp_ir")},
        ):
            arguments: dict[str, object] = {
                "original_command": "collect-readonly-receipt",
                "arguments": (),
                "account_name": "preflight",
                "account_uid": 999,
                "request_bytes": _request(),
            }
            arguments.update(changes)
            with self.subTest(changes=changes), self.assertRaises(
                boundary.ReceiptAgentBoundaryError,
            ):
                dispatcher.dispatch(**arguments)  # type: ignore[arg-type]
        self.assertEqual([], runner.calls)

        for result in (
            boundary.ReceiptAgentDispatcherRunnerResult(2, b""),
            boundary.ReceiptAgentDispatcherRunnerResult(0, b"not-a-receipt\n"),
            boundary.ReceiptAgentDispatcherRunnerResult(0, _receipt("witness")),
            boundary.ReceiptAgentDispatcherRunnerResult(0, b"x" * (32 * 1024 + 1)),
        ):
            with self.subTest(result=result.exit_code), self.assertRaises(
                boundary.ReceiptAgentBoundaryError,
            ):
                boundary.ReceiptAgentDispatcher(
                    agent_release_sha=AGENT_RELEASE_SHA,
                    runner=_Runner(result),
                ).dispatch(
                    original_command="collect-readonly-receipt",
                    arguments=(),
                    account_name="preflight",
                    account_uid=999,
                    request_bytes=_request(),
                )

    def test_witness_only_literal_evidence_account_is_rendered_separately_and_has_no_selector(self) -> None:
        rendered = boundary.render_receipt_agent_assets(_config(site_role="witness"))
        witness_runtime = json.loads(
            rendered.file(boundary.FIXED_WITNESS_EVIDENCE_ROOT_COLLECTOR_CONFIG)
        )
        self.assertEqual(witness_runtime["site_role"], "witness")
        self.assertFalse(witness_runtime["enabled"])
        self.assertEqual(
            boundary.WitnessEvidenceAgentRuntimeConfig(
                enabled=False,
                site_role="witness",
                agent_release_sha=AGENT_RELEASE_SHA,
            ),
            boundary.parse_witness_evidence_agent_runtime_config(witness_runtime),
        )
        sshd = rendered.file(boundary.FIXED_WITNESS_EVIDENCE_SSHD_CONFIG).decode("ascii")
        self.assertIn("Match User preflight-witness-evidence", sshd)
        self.assertIn("ForceCommand exec /usr/bin/python3 -I ", sshd)
        self.assertIn("DisableForwarding yes", sshd)
        self.assertIn("PermitOpen none", sshd)
        self.assertIn("PermitListen none", sshd)
        self.assertIn("Match all", sshd)
        self.assertNotIn("collect-readonly-receipt", sshd)
        force_shell = rendered.file(boundary.FIXED_WITNESS_EVIDENCE_FORCE_SHELL).decode("ascii")
        expected_force_command = sshd.split("ForceCommand ", 1)[1].split("\n", 1)[0]
        accepted = subprocess.run(
            ["/bin/sh", "-c", force_shell, "evidence-force-shell", "-c", expected_force_command],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertIn(accepted.returncode, {2, 127})
        rejected = subprocess.run(
            ["/bin/sh", "-c", force_shell, "evidence-force-shell", "-c", "id"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertEqual(rejected.returncode, 126)

        fake_evidence = canonical_json_bytes(
            {"schema": boundary.DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_SCHEMA}
        ) + b"\n"
        runner = _WitnessEvidenceRunner(
            boundary.WitnessEvidenceAgentDispatcherRunnerResult(0, fake_evidence)
        )
        result = boundary.WitnessEvidenceAgentDispatcher(
            agent_release_sha=AGENT_RELEASE_SHA,
            runner=runner,
        ).dispatch(
            original_command=boundary.FIXED_WITNESS_EVIDENCE_COLLECTOR_COMMAND,
            arguments=(),
            account_name=boundary.FIXED_WITNESS_EVIDENCE_ACCOUNT,
            account_uid=998,
            stdin_bytes=b"",
        )
        self.assertEqual(result, fake_evidence)
        self.assertEqual(len(runner.calls), 1)
        invocation = runner.calls[0]
        self.assertEqual(invocation.stdin_bytes, b"")
        self.assertEqual(
            invocation.arguments[-1],
            str(boundary.witness_evidence_agent_source_paths(AGENT_RELEASE_SHA)[1]),
        )
        for bad in (
            {"original_command": "collect-readonly-receipt"},
            {"account_name": "preflight"},
            {"stdin_bytes": b"selector"},
        ):
            arguments: dict[str, object] = {
                "original_command": boundary.FIXED_WITNESS_EVIDENCE_COLLECTOR_COMMAND,
                "arguments": (),
                "account_name": boundary.FIXED_WITNESS_EVIDENCE_ACCOUNT,
                "account_uid": 998,
                "stdin_bytes": b"",
            }
            arguments.update(bad)
            with self.subTest(bad=bad), self.assertRaises(boundary.ReceiptAgentBoundaryError):
                boundary.WitnessEvidenceAgentDispatcher(
                    agent_release_sha=AGENT_RELEASE_SHA,
                    runner=runner,
                ).dispatch(**arguments)  # type: ignore[arg-type]

        fi_rendered = boundary.render_receipt_agent_assets(_config(site_role="bot_fi"))
        self.assertNotIn(
            boundary.FIXED_WITNESS_EVIDENCE_ROOT_COLLECTOR_CONFIG,
            [item.destination for item in fi_rendered.files],
        )

    def test_renderer_and_root_collector_are_local_only_and_dispatcher_has_no_shell(self) -> None:
        core_source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(core_source, filename=str(MODULE_PATH))
        imports = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertTrue(
            imports.isdisjoint(
                {
                    "asyncio",
                    "boto3",
                    "botocore",
                    "docker",
                    "http",
                    "paramiko",
                    "socket",
                    "subprocess",
                    "urllib",
                }
            )
        )

        for path in (WITNESS_EVIDENCE_DISPATCHER_PATH, WITNESS_EVIDENCE_ROOT_COLLECTOR_PATH):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            imports = {
                alias.name.split(".", 1)[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            } | {
                node.module.split(".", 1)[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
            self.assertTrue(
                imports.isdisjoint(
                    {"boto3", "docker", "http", "paramiko", "socket", "urllib"}
                )
            )

        dispatcher_source = DISPATCHER_PATH.read_text(encoding="utf-8")
        dispatcher_tree = ast.parse(dispatcher_source, filename=str(DISPATCHER_PATH))
        self.assertNotIn("os.system", dispatcher_source)
        popen_calls = [
            node
            for node in ast.walk(dispatcher_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Popen"
        ]
        self.assertEqual(1, len(popen_calls))
        self.assertTrue(
            any(
                keyword.arg == "shell"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False
                for keyword in popen_calls[0].keywords
            )
        )
        self.assertIn("stderr=subprocess.DEVNULL", dispatcher_source)
        self.assertIn("MAX_RECEIPT_BYTES + 1", dispatcher_source)

        root_source = ROOT_COLLECTOR_PATH.read_text(encoding="utf-8")
        root_tree = ast.parse(root_source, filename=str(ROOT_COLLECTOR_PATH))
        root_imports = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(root_tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".", 1)[0]
            for node in ast.walk(root_tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertTrue(
            root_imports.isdisjoint(
                {"boto3", "docker", "http", "paramiko", "socket", "subprocess", "urllib"}
            )
        )
        self.assertIn("runtime.enabled is not True", root_source)
        self.assertIn("request.role != runtime.site_role", root_source)
        self.assertNotIn("webapp_ir", root_source.lower())


if __name__ == "__main__":
    unittest.main()
