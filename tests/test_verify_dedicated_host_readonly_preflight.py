"""Tests for the local-only dedicated-host receipt aggregate verifier."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from core.dedicated_host_preflight_receipt import canonical_json_bytes
from tests.test_dedicated_host_preflight_manifest import manifest as fixture_manifest
from tests.test_dedicated_host_preflight_receipt import valid_receipt


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_dedicated_host_readonly_preflight.py"
SPEC = importlib.util.spec_from_file_location("verify_dedicated_host_readonly_preflight", MODULE_PATH)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


class DedicatedHostReadOnlyPreflightVerifierTests(unittest.TestCase):
    def test_manifest_projection_is_exact_and_url_free(self) -> None:
        manifest = fixture_manifest()
        binding = verifier.validated_manifest_binding(canonical_json_bytes(manifest) + b"\n")

        self.assertEqual(binding["campaign_id"], manifest["campaign_id"])
        self.assertEqual(binding["operation_id"], manifest["operation_id"])
        self.assertEqual(binding["release_sha"], manifest["release_sha"])
        self.assertEqual([item["role"] for item in binding["roles"]], list(verifier.ROLE_ORDER))
        self.assertNotIn(b"url", canonical_json_bytes(binding).lower())

    def test_aggregate_reads_exactly_four_root_only_inputs_in_order(self) -> None:
        manifest = fixture_manifest()
        binding = verifier.validated_manifest_binding(canonical_json_bytes(manifest) + b"\n")
        inputs = [canonical_json_bytes(manifest) + b"\n"]
        for role in binding["roles"]:
            item = valid_receipt()
            item["campaign_id"] = binding["campaign_id"]
            item["operation_id"] = binding["operation_id"]
            item["release_sha"] = binding["release_sha"]
            item["manifest_sha256"] = binding["manifest_sha256"]
            item["role"] = role["role"]
            item["instance"]["server_id"] = role["instance_id"]
            item["instance"]["public_ipv4"] = role["public_ipv4"]
            item["observation"]["role_marker"] = role["role"]
            item["observation"]["release"]["release_sha"] = binding["release_sha"]
            inputs.append(canonical_json_bytes(item) + b"\n")
        iterator = iter(inputs)
        with (
            patch.object(verifier, "_require_root"),
            patch.object(verifier, "_read_root_only_file", side_effect=lambda *args, **kwargs: next(iterator)),
        ):
            aggregate = verifier.aggregate_files(
                manifest_path=Path("/safe/manifest.json"),
                receipt_paths=[Path(f"/safe/{role}.json") for role in verifier.ROLE_ORDER],
            )

        self.assertEqual(aggregate["campaign_id"], manifest["campaign_id"])
        self.assertEqual(aggregate["operation_id"], manifest["operation_id"])
        self.assertEqual(aggregate["decision"], "not-evaluated")
        self.assertEqual([item["role"] for item in aggregate["receipts"]], list(verifier.ROLE_ORDER))

    def test_wrong_receipt_count_or_manifest_fails_closed(self) -> None:
        with patch.object(verifier, "_require_root"):
            with self.assertRaisesRegex(verifier.DedicatedHostPreflightVerificationError, "four"):
                verifier.aggregate_files(manifest_path=Path("/safe/manifest"), receipt_paths=[])
        with self.assertRaisesRegex(verifier.DedicatedHostPreflightVerificationError, "invalid"):
            verifier.validated_manifest_binding(b'{"schema":"bad"}\n')

    def test_aggregate_rejects_noncanonical_receipt_bytes(self) -> None:
        manifest = fixture_manifest()
        binding = verifier.validated_manifest_binding(canonical_json_bytes(manifest) + b"\n")
        inputs = [canonical_json_bytes(manifest) + b"\n"]
        for index, role in enumerate(binding["roles"]):
            item = valid_receipt()
            item["campaign_id"] = binding["campaign_id"]
            item["operation_id"] = binding["operation_id"]
            item["release_sha"] = binding["release_sha"]
            item["manifest_sha256"] = binding["manifest_sha256"]
            item["role"] = role["role"]
            item["instance"]["server_id"] = role["instance_id"]
            item["instance"]["public_ipv4"] = role["public_ipv4"]
            item["observation"]["role_marker"] = role["role"]
            item["observation"]["release"]["release_sha"] = binding["release_sha"]
            if index == 0:
                inputs.append(b"{\"schema\":\"not-canonical\"}\n")
            else:
                inputs.append(canonical_json_bytes(item) + b"\n")
        iterator = iter(inputs)
        with (
            patch.object(verifier, "_require_root"),
            patch.object(verifier, "_read_root_only_file", side_effect=lambda *args, **kwargs: next(iterator)),
            self.assertRaisesRegex(verifier.DedicatedHostPreflightVerificationError, "receipts"),
        ):
            verifier.aggregate_files(
                manifest_path=Path("/safe/manifest.json"),
                receipt_paths=[Path(f"/safe/{role}.json") for role in verifier.ROLE_ORDER],
            )

    def test_source_has_no_network_process_or_write_capability(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertFalse(imports & {"boto3", "botocore", "docker", "http", "requests", "socket", "subprocess", "urllib"})
        forbidden_calls = {"mkdir", "remove", "rename", "replace", "run", "system", "unlink", "write_bytes", "write_text"}
        self.assertFalse(
            any(
                isinstance(node, ast.Call)
                and (
                    isinstance(node.func, ast.Name) and node.func.id in forbidden_calls
                    or isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_calls
                )
                for node in ast.walk(tree)
            )
        )


if __name__ == "__main__":
    unittest.main()
