"""Tests for the local-only disposable four-host preflight contract."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import sys
import unittest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "dedicated_host_preflight_manifest.py"
)
SPEC = importlib.util.spec_from_file_location("dedicated_host_preflight_manifest", MODULE_PATH)
assert SPEC and SPEC.loader
contract = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = contract
SPEC.loader.exec_module(contract)


def manifest() -> dict[str, object]:
    return {
        "schema": contract.MANIFEST_SCHEMA,
        "mode": contract.PREFLIGHT_MODE,
        "campaign_id": "5e310a4a-98db-4ed8-9f47-a3c65f421c2c",
        "operation_id": "f6d5dabe-9c52-4517-b6de-7ebbc55355c9",
        "release_sha": "a" * 40,
        "hosts": [
            {"role": role, **dict(contract.EXPECTED_HOSTS[role])}
            for role in contract.ROLE_ORDER
        ],
        "production_boundaries": {
            "host_ips": list(contract.KNOWN_PRODUCTION_HOST_IPS),
            "instance_ids": [],
        },
        "known_production_boundary_sha256": contract.known_production_boundary_sha256(),
        "capabilities": {field: False for field in sorted(contract.CAPABILITY_FIELDS)},
    }


class DedicatedHostPreflightManifestTests(unittest.TestCase):
    def test_exact_four_host_manifest_is_valid_and_deterministic(self) -> None:
        candidate = manifest()

        checked = contract.validate_manifest(candidate)

        self.assertEqual(contract.ROLE_ORDER, tuple(item["role"] for item in checked["hosts"]))
        self.assertEqual(contract.MANIFEST_SCHEMA, checked["schema"])
        self.assertTrue(all(value is False for value in checked["capabilities"].values()))
        self.assertEqual(contract.manifest_sha256(candidate), contract.manifest_sha256(checked))

    def test_campaign_id_accepts_the_current_prefixed_campaign_form(self) -> None:
        candidate = manifest()
        candidate["campaign_id"] = "wa-ir-standby-97265988-4b12-444e-abda-165573b2769f"

        checked = contract.validate_manifest(candidate)

        self.assertEqual(checked["campaign_id"], candidate["campaign_id"])

        candidate["campaign_id"] = "WA-IR"
        with self.assertRaisesRegex(contract.DedicatedHostPreflightError, "campaign_id"):
            contract.validate_manifest(candidate)

    def test_readonly_requests_are_derived_only_from_the_validated_manifest(self) -> None:
        candidate = manifest()

        requests = contract.build_readonly_requests(candidate)

        self.assertEqual(len(requests), 4)
        self.assertEqual([item["role"] for item in requests], list(contract.ROLE_ORDER))
        self.assertEqual(
            {item["manifest_sha256"] for item in requests},
            {contract.manifest_sha256(candidate)},
        )
        self.assertTrue(
            all(
                set(item)
                == {
                    "schema",
                    "campaign_id",
                    "operation_id",
                    "release_sha",
                    "role",
                    "manifest_sha256",
                }
                and item["schema"] == contract.READONLY_REQUEST_SCHEMA
                and item["operation_id"] == candidate["operation_id"]
                for item in requests
            )
        )

        changed = manifest()
        changed["hosts"][0]["public_ip"] = "127.0.0.1"
        with self.assertRaises(contract.DedicatedHostPreflightError):
            contract.build_readonly_requests(changed)

    def test_each_role_is_pinned_to_its_exact_instance_ip_and_region(self) -> None:
        for index, field, replacement in (
            (0, "instance_id", "11111111-1111-4111-8111-111111111111"),
            (1, "public_ip", "203.0.113.10"),
            (2, "region", "eu-west1-a"),
            (3, "role", "webapp_ir"),
        ):
            with self.subTest(index=index, field=field):
                candidate = manifest()
                candidate["hosts"][index][field] = replacement
                with self.assertRaisesRegex(contract.DedicatedHostPreflightError, "host"):
                    contract.validate_manifest(candidate)

    def test_production_boundary_is_source_owned_complete_and_disjoint(self) -> None:
        candidate = manifest()
        candidate["production_boundaries"]["host_ips"].pop()
        with self.assertRaisesRegex(contract.DedicatedHostPreflightError, "omits"):
            contract.validate_manifest(candidate)

        candidate = manifest()
        candidate["production_boundaries"]["host_ips"].append(
            contract.EXPECTED_HOSTS["bot_fi"]["public_ip"]
        )
        candidate["production_boundaries"]["host_ips"].sort()
        with self.assertRaisesRegex(contract.DedicatedHostPreflightError, "overlaps"):
            contract.validate_manifest(candidate)

        candidate = manifest()
        candidate["production_boundaries"]["instance_ids"] = [
            contract.EXPECTED_HOSTS["witness"]["instance_id"]
        ]
        with self.assertRaisesRegex(contract.DedicatedHostPreflightError, "overlaps"):
            contract.validate_manifest(candidate)

        candidate = manifest()
        candidate["known_production_boundary_sha256"] = "0" * 64
        with self.assertRaisesRegex(contract.DedicatedHostPreflightError, "boundary digest"):
            contract.validate_manifest(candidate)

    def test_manifest_rejects_capability_escalation_and_schema_drift(self) -> None:
        candidate = manifest()
        candidate["capabilities"]["remote_execution"] = True
        with self.assertRaisesRegex(contract.DedicatedHostPreflightError, "deny every"):
            contract.validate_manifest(candidate)

        candidate = manifest()
        candidate["command"] = ["should", "never", "exist"]
        with self.assertRaisesRegex(contract.DedicatedHostPreflightError, "fields differ"):
            contract.validate_manifest(candidate)

        candidate = manifest()
        candidate["operation_id"] = candidate["campaign_id"]
        with self.assertRaisesRegex(contract.DedicatedHostPreflightError, "must differ"):
            contract.validate_manifest(candidate)

    def test_payload_is_canonical_and_duplicate_keys_fail_closed(self) -> None:
        candidate = manifest()
        payload = contract.canonical_json_bytes(candidate) + b"\n"
        self.assertEqual(candidate, contract.parse_manifest_payload(payload))
        self.assertEqual(
            candidate,
            contract.validate_manifest(contract.parse_manifest_payload(payload)),
        )

        noncanonical = json.dumps(candidate, sort_keys=False).encode("ascii") + b"\n"
        with self.assertRaisesRegex(contract.DedicatedHostPreflightError, "canonical"):
            contract.parse_manifest_payload(noncanonical)

        with self.assertRaisesRegex(contract.DedicatedHostPreflightError, "duplicate"):
            contract.parse_manifest_payload(b'{"schema":"x","schema":"y"}\n')

    def test_contract_has_no_external_effect_capability(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        forbidden_imports = {
            "boto3",
            "botocore",
            "docker",
            "http",
            "os",
            "paramiko",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
        self.assertFalse(imports & forbidden_imports)
        forbidden_calls = {
            "open",
            "read_text",
            "read_bytes",
            "write_text",
            "write_bytes",
            "run",
            "Popen",
            "system",
        }
        self.assertFalse(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in forbidden_calls
                for node in ast.walk(tree)
            )
        )


if __name__ == "__main__":
    unittest.main()
