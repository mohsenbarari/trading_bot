"""Tests for the retired WA-IR Object-Storage receipt-route tombstone."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from core import dedicated_host_preflight_controller as controller
from core import dedicated_host_preflight_ir_object_storage_pull_delivery as retired
from scripts.dedicated_host_preflight_manifest import (
    CAPABILITY_FIELDS,
    EXPECTED_HOSTS,
    KNOWN_PRODUCTION_HOST_IPS,
    MANIFEST_SCHEMA,
    PREFLIGHT_MODE,
    ROLE_ORDER,
    build_readonly_requests,
    known_production_boundary_sha256,
    validate_manifest,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "dedicated_host_preflight_ir_object_storage_pull_delivery.py"
)


def _controller_config() -> dict[str, object]:
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


def _manifest() -> dict[str, object]:
    return {
        "schema": MANIFEST_SCHEMA,
        "mode": PREFLIGHT_MODE,
        "campaign_id": "dedicated-preflight-20260731",
        "operation_id": "e85a1b86-7d55-4d32-8a27-15a21700394f",
        "release_sha": "a" * 40,
        "hosts": [{"role": role, **dict(EXPECTED_HOSTS[role])} for role in ROLE_ORDER],
        "production_boundaries": {
            "host_ips": list(KNOWN_PRODUCTION_HOST_IPS),
            "instance_ids": [],
        },
        "known_production_boundary_sha256": known_production_boundary_sha256(),
        "capabilities": {field: False for field in CAPABILITY_FIELDS},
    }


class RetiredIrObjectStoragePullDeliveryTests(unittest.TestCase):
    def test_fixed_redacted_blocked_result_and_rejection(self) -> None:
        self.assertFalse(
            retired.DEDICATED_HOST_PREFLIGHT_IR_OBJECT_STORAGE_PULL_DELIVERY_DEFAULT_ENABLED
        )
        self.assertEqual(
            dict(retired.retired_ir_object_storage_pull_delivery_blocked_result()),
            {
                "schema": retired.RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_SCHEMA,
                "status": "blocked",
                "error": retired.RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_ERROR,
                "reason": "no-direct-or-bypass-route",
                "retired_delivery_route": retired.RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_ROUTE,
                "retired_delivery_phase": retired.RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_PHASE,
            },
        )
        with self.assertRaisesRegex(
            retired.RetiredIrObjectStoragePullDeliveryError,
            "^IR_OBJECT_STORAGE_PULL_RETIRED_NO_DIRECT_OR_BYPASS_ROUTE$",
        ):
            retired.reject_retired_ir_object_storage_pull_delivery()

    def test_active_controller_rejects_former_route_before_any_delivery(self) -> None:
        candidate = _controller_config()
        iran = candidate["hosts"][2]  # type: ignore[index]
        iran["delivery_route"] = retired.RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_ROUTE
        iran["delivery_phase"] = retired.RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_PHASE

        with self.assertRaisesRegex(
            controller.DedicatedHostPreflightControllerError,
            "retired; no direct or bypass route exists",
        ):
            controller.validate_controller_config(candidate)

        self.assertEqual(
            controller.RETIRED_DELIVERY_CONTRACT_BY_ROLE["webapp_ir"],
            frozenset(
                {
                    (
                        retired.RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_ROUTE,
                        retired.RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_PHASE,
                    )
                }
            ),
        )

    def test_candidate_manifest_has_no_delivery_selector_or_legacy_escape_hatch(self) -> None:
        candidate = _manifest()
        checked = validate_manifest(candidate)
        requests = build_readonly_requests(checked)
        self.assertTrue(
            all(
                "delivery_route" not in request and "delivery_phase" not in request
                for request in requests
            )
        )

        injected = _manifest()
        injected["hosts"][2]["delivery_route"] = (  # type: ignore[index]
            retired.RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_ROUTE
        )
        with self.assertRaisesRegex(Exception, "host binding fields differ"):
            validate_manifest(injected)

    def test_tombstone_has_no_controller_or_agent_delivery_surface(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertNotIn("core.dedicated_host_preflight_controller", imported_modules)
        imported_roots = {
            node.names[0].name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import) and node.names
        } | {module.split(".")[0] for module in imported_modules}
        self.assertTrue(
            {"boto3", "subprocess", "socket", "paramiko", "requests"}.isdisjoint(
                imported_roots
            )
        )
        self.assertFalse(hasattr(retired, "IrObjectStoragePullAgentDelivery"))
        self.assertFalse(hasattr(retired, "DedicatedHostTarget"))
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "collect_readonly_receipt",
            "get_object(",
            "AgentDelivery",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
