"""Tests for the retired concrete WA-IR Object-Storage receipt runtime."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from core import dedicated_host_preflight_ir_object_storage_pull_delivery as retired_pull
from core import dedicated_host_preflight_ir_object_storage_runtime as retired_runtime


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "dedicated_host_preflight_ir_object_storage_runtime.py"
)


class RetiredIrObjectStorageRuntimeTests(unittest.TestCase):
    def test_fixed_redacted_blocked_result_and_rejection(self) -> None:
        self.assertFalse(
            retired_runtime.DEDICATED_HOST_PREFLIGHT_IR_OBJECT_STORAGE_RUNTIME_DEFAULT_ENABLED
        )
        self.assertEqual(
            dict(retired_runtime.retired_ir_object_storage_runtime_blocked_result()),
            {
                "schema": retired_runtime.RETIRED_IR_OBJECT_STORAGE_RUNTIME_SCHEMA,
                "status": "blocked",
                "error": retired_runtime.RETIRED_IR_OBJECT_STORAGE_RUNTIME_ERROR,
                "reason": "no-direct-or-bypass-route",
                "retired_delivery_route": retired_pull.RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_ROUTE,
                "retired_delivery_phase": retired_pull.RETIRED_IR_OBJECT_STORAGE_PULL_DELIVERY_PHASE,
            },
        )
        with self.assertRaisesRegex(
            retired_runtime.RetiredIrObjectStorageRuntimeError,
            "^IR_OBJECT_STORAGE_RUNTIME_RETIRED_NO_DIRECT_OR_BYPASS_ROUTE$",
        ):
            retired_runtime.reject_retired_ir_object_storage_runtime()

    def test_runtime_has_no_provisioner_or_generic_delivery_bridge(self) -> None:
        self.assertFalse(
            hasattr(retired_runtime, "provision_root_owned_ir_object_storage_pull_delivery")
        )
        self.assertFalse(
            hasattr(retired_runtime, "load_and_provision_root_owned_ir_object_storage_pull_delivery")
        )
        self.assertFalse(hasattr(retired_runtime, "ProvisionedIrObjectStoragePullAgentDelivery"))

        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertNotIn("core.dedicated_host_preflight_controller", imported_modules)
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "collect_readonly_receipt",
            "get_object(",
            "AgentDelivery",
            "boto3",
            "botocore",
            "subprocess",
            "socket",
            "paramiko",
            "requests",
            "credential_admitter",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
