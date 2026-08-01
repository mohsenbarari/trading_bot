"""Tests for the pure, transport-injected four-host preflight controller."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
from pathlib import Path
import unittest

from core import dedicated_host_preflight_controller as controller
from core.dedicated_host_preflight_receipt import (
    PREFLIGHT_RECEIPT_SCHEMA,
    canonical_json_bytes,
)
from scripts.dedicated_host_preflight_manifest import (
    CAPABILITY_FIELDS,
    EXPECTED_HOSTS,
    KNOWN_PRODUCTION_HOST_IPS,
    MANIFEST_SCHEMA,
    PREFLIGHT_MODE,
    ROLE_ORDER,
    known_production_boundary_sha256,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "dedicated_host_preflight_controller.py"
)


def preflight_manifest() -> dict[str, object]:
    """Use source-owned identities without duplicating live values in tests."""

    return {
        "schema": MANIFEST_SCHEMA,
        "mode": PREFLIGHT_MODE,
        "campaign_id": "dedicated-preflight-20260731",
        "operation_id": "e85a1b86-7d55-4d32-8a27-15a21700394f",
        "release_sha": "a" * 40,
        "hosts": [
            {"role": role, **dict(EXPECTED_HOSTS[role])} for role in ROLE_ORDER
        ],
        "production_boundaries": {
            "host_ips": list(KNOWN_PRODUCTION_HOST_IPS),
            "instance_ids": [],
        },
        "known_production_boundary_sha256": known_production_boundary_sha256(),
        "capabilities": {field: False for field in CAPABILITY_FIELDS},
    }


def controller_config() -> dict[str, object]:
    hosts: list[dict[str, str]] = []
    for index, role in enumerate(ROLE_ORDER, start=1):
        expected = EXPECTED_HOSTS[role]
        route, phase = controller.DELIVERY_CONTRACT_BY_ROLE[role]
        hosts.append(
            {
                "role": role,
                "instance_id": expected["instance_id"],
                "public_ipv4": expected["public_ip"],
                "region": expected["region"],
                # Test-only synthetic fingerprints; no production host key is
                # stored in the repository or emitted by this test.
                "host_key_sha256": format(index, "x") * 64,
                "delivery_route": route,
                "delivery_phase": phase,
            }
        )
    return {
        "schema": controller.CONTROLLER_CONFIG_SCHEMA,
        "mode": "read-only",
        "provider": {"name": "arvan_ecc", "readback": "get-only"},
        "hosts": hosts,
    }


def receipt_for(
    target: controller.DedicatedHostTarget, request: dict[str, str]
) -> dict[str, object]:
    return {
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


class FakeProvider:
    """In-memory contract double: it deliberately performs no transport."""

    def __init__(self, change=None) -> None:
        self.change = change
        self.calls: list[controller.DedicatedHostTarget] = []

    async def readback(
        self, *, target: controller.DedicatedHostTarget
    ) -> dict[str, object]:
        self.calls.append(target)
        document = {
            "schema": controller.PROVIDER_READBACK_SCHEMA,
            "role": target.role,
            "provider": "arvan_ecc",
            "instance_id": target.instance_id,
            "public_ipv4": target.public_ipv4,
            "region": target.region,
            "status": "running",
        }
        raw_readback = canonical_json_bytes(document) + b"\n"
        response: dict[str, object] = {
            "schema": controller.PROVIDER_READBACK_RESPONSE_SCHEMA,
            "role": target.role,
            "provider": "arvan_ecc",
            "readback_mode": "get-only",
            "readback_path": controller.PROVIDER_READBACK_PATH_BY_ROLE[target.role],
            "readback_sha256": hashlib.sha256(raw_readback).hexdigest(),
            "readback_bytes": raw_readback,
        }
        return self.change(response, target) if self.change else response


class FailingProvider:
    async def readback(self, *, target: controller.DedicatedHostTarget) -> dict[str, object]:
        del target
        raise RuntimeError("transport unavailable")


class FakeDelivery:
    """In-memory raw-byte delivery double with metadata binding by default."""

    def __init__(self, change=None) -> None:
        self.change = change
        self.calls: list[tuple[controller.DedicatedHostTarget, bytes, str, str]] = []

    async def collect_readonly_receipt(
        self,
        *,
        target: controller.DedicatedHostTarget,
        request_bytes: bytes,
        request_sha256: str,
        receipt_path: str,
    ) -> dict[str, object]:
        self.calls.append((target, request_bytes, request_sha256, receipt_path))
        request = json.loads(request_bytes)
        raw_receipt = canonical_json_bytes(receipt_for(target, request)) + b"\n"
        response: dict[str, object] = {
            "schema": controller.AGENT_DELIVERY_RESPONSE_SCHEMA,
            "role": target.role,
            "delivery_route": target.delivery_route,
            "delivery_phase": target.delivery_phase,
            "host_key_sha256": target.host_key_sha256,
            "request_sha256": request_sha256,
            "receipt_path": receipt_path,
            "receipt_sha256": hashlib.sha256(raw_receipt).hexdigest(),
            "receipt_bytes": raw_receipt,
        }
        return self.change(response, target, request) if self.change else response


class ReusedReceiptDelivery(FakeDelivery):
    """Returns a prior raw receipt while making current metadata look valid."""

    def __init__(self) -> None:
        super().__init__()
        self.first_raw_receipt: bytes | None = None

    async def collect_readonly_receipt(self, **kwargs: object) -> dict[str, object]:
        response = await super().collect_readonly_receipt(**kwargs)  # type: ignore[arg-type]
        target = kwargs["target"]
        assert isinstance(target, controller.DedicatedHostTarget)
        raw_receipt = response["receipt_bytes"]
        assert isinstance(raw_receipt, bytes)
        if self.first_raw_receipt is None:
            self.first_raw_receipt = raw_receipt
        elif target.role == "webapp_fi":
            response["receipt_bytes"] = self.first_raw_receipt
            response["receipt_sha256"] = hashlib.sha256(self.first_raw_receipt).hexdigest()
        return response


class ReusedReadbackProvider(FakeProvider):
    """Reuses a prior raw provider document under the next role metadata."""

    def __init__(self) -> None:
        super().__init__()
        self.first_raw_readback: bytes | None = None

    async def readback(self, **kwargs: object) -> dict[str, object]:
        response = await super().readback(**kwargs)  # type: ignore[arg-type]
        raw_readback = response["readback_bytes"]
        assert isinstance(raw_readback, bytes)
        if self.first_raw_readback is None:
            self.first_raw_readback = raw_readback
        else:
            response["readback_bytes"] = self.first_raw_readback
            response["readback_sha256"] = hashlib.sha256(
                self.first_raw_readback
            ).hexdigest()
        return response


def replace_raw_provider_document(
    response: dict[str, object], **changes: str
) -> dict[str, object]:
    """Make a canonical tampered raw document while retaining safe wrapper data."""

    raw_readback = response["readback_bytes"]
    assert isinstance(raw_readback, bytes)
    document = json.loads(raw_readback)
    document.update(changes)
    changed_raw = canonical_json_bytes(document) + b"\n"
    return {
        **response,
        "readback_bytes": changed_raw,
        "readback_sha256": hashlib.sha256(changed_raw).hexdigest(),
    }


class DedicatedHostPreflightControllerTests(unittest.TestCase):
    def test_valid_four_host_observation_is_bound_but_not_authorizing(self) -> None:
        provider = FakeProvider()
        delivery = FakeDelivery()

        result = asyncio.run(
            controller.run_preflight_controller(
                config=controller_config(),
                manifest=preflight_manifest(),
                provider_readback=provider,
                agent_delivery=delivery,
            )
        )

        self.assertEqual(result["schema"], controller.CONTROLLER_RESULT_SCHEMA)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["observation_mode"], "read-only")
        self.assertEqual(result["aggregate"]["decision"], "not-evaluated")
        self.assertEqual(
            [item["role"] for item in result["provider_readbacks"]], list(ROLE_ORDER)
        )
        self.assertEqual(
            [item["readback"]["role"] for item in result["provider_readbacks"]],
            list(ROLE_ORDER),
        )
        self.assertEqual(
            [item["role"] for item in result["delivery_provenance"]], list(ROLE_ORDER)
        )
        self.assertEqual(len(provider.calls), 4)
        self.assertEqual(len(delivery.calls), 4)
        self.assertNotIn("ready", json.dumps(result, sort_keys=True).lower())
        self.assertNotIn("passed", json.dumps(result, sort_keys=True).lower())

    def test_config_accepts_only_exact_source_pinned_hosts_and_unique_host_keys(self) -> None:
        checked = controller.validate_controller_config(controller_config())
        self.assertEqual(tuple(target.role for target in checked.targets), ROLE_ORDER)

        mismatched = controller_config()
        mismatched["hosts"][0]["region"] = "other-region"  # type: ignore[index]
        with self.assertRaisesRegex(
            controller.DedicatedHostPreflightControllerError, "source-pinned"
        ):
            controller.validate_controller_config(mismatched)

        duplicate_key = controller_config()
        duplicate_key["hosts"][1]["host_key_sha256"] = duplicate_key["hosts"][0][  # type: ignore[index]
            "host_key_sha256"
        ]
        with self.assertRaisesRegex(
            controller.DedicatedHostPreflightControllerError, "fingerprints"
        ):
            controller.validate_controller_config(duplicate_key)

        injected = controller_config()
        injected["command"] = "not-allowed"
        with self.assertRaises(controller.DedicatedHostPreflightControllerError):
            controller.validate_controller_config(injected)

    def test_iran_route_is_only_dual_signed_witness_evidence_before_any_observer(self) -> None:
        config = controller_config()
        iran = config["hosts"][2]  # type: ignore[index]
        iran["delivery_route"] = "pinned-ssh-readonly-agent"
        iran["delivery_phase"] = "collect-readonly-receipt"
        provider = FakeProvider()

        with self.assertRaisesRegex(
            controller.DedicatedHostPreflightControllerError, "Finland-to-Iran"
        ):
            asyncio.run(
                controller.run_preflight_controller(
                    config=config,
                    manifest=preflight_manifest(),
                    provider_readback=provider,
                    agent_delivery=FakeDelivery(),
                )
            )
        self.assertEqual(provider.calls, [])

        exact_route, exact_phase = controller.DELIVERY_CONTRACT_BY_ROLE["webapp_ir"]
        self.assertEqual(exact_route, "witness-dual-signed-preflight-evidence")
        self.assertEqual(exact_phase, "collect-wa-ir-witness-preflight-evidence")

    def test_provider_identity_mismatch_and_provider_failure_fail_closed(self) -> None:
        wrong_region = FakeProvider(
            lambda response, _target: replace_raw_provider_document(
                response, region="unexpected-region"
            )
        )
        with self.assertRaisesRegex(
            controller.DedicatedHostPreflightControllerError, "pinned running host"
        ):
            asyncio.run(
                controller.run_preflight_controller(
                    config=controller_config(),
                    manifest=preflight_manifest(),
                    provider_readback=wrong_region,
                    agent_delivery=FakeDelivery(),
                )
            )

        blocked = asyncio.run(
            controller.observe_preflight_controller(
                config=controller_config(),
                manifest=preflight_manifest(),
                provider_readback=FailingProvider(),
                agent_delivery=FakeDelivery(),
            )
        )
        self.assertEqual(
            blocked,
            {
                "schema": controller.CONTROLLER_RESULT_SCHEMA,
                "status": "blocked",
                "observation_mode": "read-only",
            },
        )

    def test_provider_raw_bytes_hash_tamper_and_reuse_fail_closed(self) -> None:
        hash_mismatch = FakeProvider(
            lambda response, _target: {**response, "readback_sha256": "0" * 64}
        )
        with self.assertRaisesRegex(
            controller.DedicatedHostPreflightControllerError, "provenance"
        ):
            asyncio.run(
                controller.run_preflight_controller(
                    config=controller_config(),
                    manifest=preflight_manifest(),
                    provider_readback=hash_mismatch,
                    agent_delivery=FakeDelivery(),
                )
            )

        noncanonical = FakeProvider(
            lambda response, _target: {
                **response,
                "readback_bytes": b'{"schema":"x","schema":"y"}\n',
                "readback_sha256": hashlib.sha256(
                    b'{"schema":"x","schema":"y"}\n'
                ).hexdigest(),
            }
        )
        with self.assertRaisesRegex(
            controller.DedicatedHostPreflightControllerError, "duplicate"
        ):
            asyncio.run(
                controller.run_preflight_controller(
                    config=controller_config(),
                    manifest=preflight_manifest(),
                    provider_readback=noncanonical,
                    agent_delivery=FakeDelivery(),
                )
            )

        oversized = FakeProvider(
            lambda response, _target: {
                **response,
                "readback_bytes": b"x" * (controller.MAX_PROVIDER_READBACK_BYTES + 1),
                "readback_sha256": hashlib.sha256(
                    b"x" * (controller.MAX_PROVIDER_READBACK_BYTES + 1)
                ).hexdigest(),
            }
        )
        with self.assertRaisesRegex(
            controller.DedicatedHostPreflightControllerError, "bounded raw response"
        ):
            asyncio.run(
                controller.run_preflight_controller(
                    config=controller_config(),
                    manifest=preflight_manifest(),
                    provider_readback=oversized,
                    agent_delivery=FakeDelivery(),
                )
            )

        with self.assertRaisesRegex(
            controller.DedicatedHostPreflightControllerError, "pinned running host"
        ):
            asyncio.run(
                controller.run_preflight_controller(
                    config=controller_config(),
                    manifest=preflight_manifest(),
                    provider_readback=ReusedReadbackProvider(),
                    agent_delivery=FakeDelivery(),
                )
            )

    def test_delivery_host_key_raw_bytes_and_role_receipt_bindings_fail_closed(self) -> None:
        wrong_key = FakeDelivery(
            lambda response, _target, _request: {
                **response,
                "host_key_sha256": "f" * 64,
            }
        )
        with self.assertRaisesRegex(
            controller.DedicatedHostPreflightControllerError, "metadata"
        ):
            asyncio.run(
                controller.run_preflight_controller(
                    config=controller_config(),
                    manifest=preflight_manifest(),
                    provider_readback=FakeProvider(),
                    agent_delivery=wrong_key,
                )
            )

        non_bytes = FakeDelivery(
            lambda response, _target, _request: {
                **response,
                "receipt_bytes": {"not": "raw-bytes"},
            }
        )
        with self.assertRaisesRegex(
            controller.DedicatedHostPreflightControllerError, "raw receipt bytes"
        ):
            asyncio.run(
                controller.run_preflight_controller(
                    config=controller_config(),
                    manifest=preflight_manifest(),
                    provider_readback=FakeProvider(),
                    agent_delivery=non_bytes,
                )
            )

        receipt_hash_mismatch = FakeDelivery(
            lambda response, _target, _request: {
                **response,
                "receipt_sha256": "0" * 64,
            }
        )
        with self.assertRaisesRegex(
            controller.DedicatedHostPreflightControllerError, "metadata"
        ):
            asyncio.run(
                controller.run_preflight_controller(
                    config=controller_config(),
                    manifest=preflight_manifest(),
                    provider_readback=FakeProvider(),
                    agent_delivery=receipt_hash_mismatch,
                )
            )

        oversized_receipt = FakeDelivery(
            lambda response, _target, _request: {
                **response,
                "receipt_bytes": b"x" * (controller.MAX_RECEIPT_BYTES + 1),
                "receipt_sha256": hashlib.sha256(
                    b"x" * (controller.MAX_RECEIPT_BYTES + 1)
                ).hexdigest(),
            }
        )
        with self.assertRaisesRegex(
            controller.DedicatedHostPreflightControllerError, "bounded raw receipt"
        ):
            asyncio.run(
                controller.run_preflight_controller(
                    config=controller_config(),
                    manifest=preflight_manifest(),
                    provider_readback=FakeProvider(),
                    agent_delivery=oversized_receipt,
                )
            )

        with self.assertRaisesRegex(
            controller.DedicatedHostPreflightControllerError, "receipt bytes"
        ):
            asyncio.run(
                controller.run_preflight_controller(
                    config=controller_config(),
                    manifest=preflight_manifest(),
                    provider_readback=FakeProvider(),
                    agent_delivery=ReusedReceiptDelivery(),
                )
            )

    def test_default_disabled_adapters_cannot_claim_observation(self) -> None:
        result = asyncio.run(
            controller.observe_preflight_controller(
                config=controller_config(),
                manifest=preflight_manifest(),
                provider_readback=controller.DisabledProviderReadback(),
                agent_delivery=controller.DisabledAgentDelivery(),
            )
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(set(result), {"schema", "status", "observation_mode"})

    def test_controller_core_has_no_transport_or_mutation_import_or_interface(self) -> None:
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
                "os",
                "paramiko",
                "requests",
                "socket",
                "subprocess",
                "urllib",
            }
        )

        protocol_methods = {
            node.name
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name in {"ProviderReadback", "AgentDelivery"}
            for node in node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertEqual(protocol_methods, {"readback", "collect_readonly_receipt"})

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
        self.assertNotIn("three_site_full_matrix", MODULE_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
