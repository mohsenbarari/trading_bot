"""Tests for the disabled-by-default local controller executable."""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.dedicated_host_preflight_receipt import canonical_json_bytes
from tests.test_dedicated_host_preflight_controller import (
    controller_config,
    preflight_manifest,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "run_dedicated_host_readonly_preflight_controller.py"
)
SPEC = importlib.util.spec_from_file_location(
    "run_dedicated_host_readonly_preflight_controller", MODULE_PATH
)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def write_root_only_canonical(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes(value) + b"\n")
    path.chmod(0o600)


@unittest.skipUnless(os.geteuid() == 0, "the production contract requires root")
class DedicatedHostPreflightControllerCliTests(unittest.TestCase):
    def test_root_only_canonical_loader_accepts_only_private_stable_json(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            path = Path(directory) / "controller-config.json"
            expected = controller_config()
            write_root_only_canonical(path, expected)

            self.assertEqual(
                runner.load_root_only_canonical_json(
                    path,
                    label="test controller config",
                    maximum_bytes=runner.MAX_CONFIG_BYTES,
                ),
                expected,
            )

            path.chmod(0o640)
            with self.assertRaisesRegex(
                runner.DedicatedHostPreflightControllerCliError, "mode-0600"
            ):
                runner.load_root_only_canonical_json(
                    path,
                    label="test controller config",
                    maximum_bytes=runner.MAX_CONFIG_BYTES,
                )

    def test_default_cli_is_transport_disabled_and_emits_only_blocked(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            config_path = Path(directory) / "controller-config.json"
            manifest_path = Path(directory) / "manifest.json"
            write_root_only_canonical(config_path, controller_config())
            write_root_only_canonical(manifest_path, preflight_manifest())
            output = io.BytesIO()

            with patch.object(runner.sys, "stdout", SimpleNamespace(buffer=output)):
                exit_code = runner.main(
                    ["--config", str(config_path), "--manifest", str(manifest_path)]
                )

        self.assertEqual(exit_code, 2)
        result = json.loads(output.getvalue())
        self.assertEqual(
            result,
            {
                "schema": runner.CONTROLLER_RESULT_SCHEMA,
                "status": "blocked",
                "observation_mode": "read-only",
            },
        )

    def test_explicit_runtime_switch_still_blocks_before_any_fi_transport_without_witness_evidence_verifier(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            config_path = Path(directory) / "controller-config.json"
            manifest_path = Path(directory) / "manifest.json"
            runtime_path = Path(directory) / "runtime-transport.json"
            write_root_only_canonical(config_path, controller_config())
            write_root_only_canonical(manifest_path, preflight_manifest())
            write_root_only_canonical(
                runtime_path,
                {
                    "schema": runner.runtime_transport.DEDICATED_HOST_PREFLIGHT_RUNTIME_TRANSPORT_CONFIG_SCHEMA,
                    "enabled": True,
                    "mode": "read-only",
                    "provider_transport": "fixed-https-get-only",
                    "fi_receipt_transport": "pinned-ssh-readonly-agent",
                    "ir_receipt_transport": "pinned-ssh-witness-evidence-agent",
                    "direct_finland_to_iran": "forbidden",
                },
            )
            output = io.BytesIO()
            with (
                patch.object(runner, "DEFAULT_RUNTIME_TRANSPORT_CONFIG_PATH", runtime_path),
                patch.object(
                    runner.runtime_transport,
                    "RootOwnedArvanEccHttpsGetRunner",
                ) as ecc_runner,
                patch.object(runner.sys, "stdout", SimpleNamespace(buffer=output)),
            ):
                exit_code = runner.main(
                    [
                        "--config",
                        str(config_path),
                        "--manifest",
                        str(manifest_path),
                        "--enable-root-owned-transports",
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertEqual(ecc_runner.call_count, 0)
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "schema": runner.CONTROLLER_RESULT_SCHEMA,
                "status": "blocked",
                "observation_mode": "read-only",
            },
        )

    def test_cli_exposes_only_root_controlled_input_paths(self) -> None:
        parser = runner._parser()
        destinations = {action.dest for action in parser._actions}
        self.assertEqual(
            destinations,
            {"help", "config", "manifest", "enable_root_owned_transports"},
        )
        self.assertEqual(runner.DEFAULT_CONFIG_PATH, Path(str(runner.DEFAULT_CONFIG_PATH)))
        self.assertEqual(runner.DEFAULT_MANIFEST_PATH, Path(str(runner.DEFAULT_MANIFEST_PATH)))
        self.assertEqual(
            runner.DEFAULT_RUNTIME_TRANSPORT_CONFIG_PATH,
            Path(str(runner.DEFAULT_RUNTIME_TRANSPORT_CONFIG_PATH)),
        )

    def test_cli_source_has_no_network_client_or_process_adapter(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertFalse(
            imports
            & {
                "boto3",
                "botocore",
                "docker",
                "http",
                "httpx",
                "paramiko",
                "requests",
                "socket",
                "subprocess",
                "urllib",
            }
        )

        forbidden_calls = {
            "Popen",
            "check_call",
            "check_output",
            "connect",
            "create",
            "delete",
            "execute",
            "post",
            "provision",
            "put",
            "request",
            "send",
            "system",
            "update",
            "urlopen",
        }
        self.assertFalse(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in forbidden_calls
                for node in ast.walk(tree)
            )
        )
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(
            {"DisabledProviderReadback", "DisabledAgentDelivery"}.issubset(called_names)
        )


if __name__ == "__main__":
    unittest.main()
