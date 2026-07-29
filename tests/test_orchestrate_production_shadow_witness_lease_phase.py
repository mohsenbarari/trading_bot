from __future__ import annotations

import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest import mock

from scripts import orchestrate_production_shadow_witness_lease_phase as MODULE
from tests import test_production_shadow_cutover_controller as FIXTURE


def context() -> MODULE.BridgeContext:
    manifest = copy.deepcopy(FIXTURE.manifest_payload())
    return MODULE.validate_context(manifest, "a" * 64)


class WitnessLeasePhaseBridgeTests(unittest.TestCase):
    def test_plan_is_hard_gated_and_lists_each_missing_immutable_binding(self) -> None:
        plan = MODULE.build_plan(context())
        self.assertEqual(plan["status"], "blocked")
        self.assertFalse(plan["apply_supported"])
        self.assertFalse(plan["direct_witness_ssh_file_transfer_allowed"])
        expected = [binding.identifier for binding in MODULE.REQUIRED_IMMUTABLE_BINDINGS]
        self.assertEqual(plan["missing_immutable_bindings"], expected)
        self.assertEqual(
            [row["id"] for row in plan["required_immutable_bindings"]], expected
        )
        self.assertFalse(plan["production_contacted"])
        self.assertFalse(plan["journal_mutated"])
        self.assertFalse(plan["object_storage_mutated"])

    def test_witness_and_iran_can_only_use_object_storage_for_payloads(self) -> None:
        rows = {row["role"]: row for row in MODULE.role_transport_contracts()}
        for role in ("witness", "webapp_ir"):
            self.assertEqual(
                rows[role]["payload_transport"],
                "object-storage-private-versioned-age",
            )
            self.assertEqual(rows[role]["ssh_application_payload_bytes"], 0)
            self.assertFalse(rows[role]["direct_ssh_file_transfer_allowed"])
            self.assertFalse(rows[role]["presigned_url_persisted"])

    def test_topology_drift_rejects_direct_witness_ssh_file_path(self) -> None:
        manifest = copy.deepcopy(FIXTURE.manifest_payload())
        manifest["topology"]["witness"]["transport"] = "ssh-control"
        with self.assertRaises(MODULE.WitnessLeasePhaseBridgeError):
            MODULE.validate_context(manifest, "a" * 64)

    def test_apply_is_unreachable_even_with_a_supplied_direct_transfer_invoker(self) -> None:
        invoker = mock.Mock()
        with self.assertRaisesRegex(MODULE.WitnessLeasePhaseBridgeError, "disabled"):
            MODULE.apply(
                context(),
                confirm=MODULE.APPLY_CONFIRMATION,
                invoker=invoker,
            )
        invoker.assert_not_called()

    def test_plan_cli_does_not_call_apply(self) -> None:
        stream = io.StringIO()
        bridge_context = context()
        with mock.patch.object(MODULE, "load_context", return_value=bridge_context), mock.patch.object(
            MODULE, "apply"
        ) as apply, redirect_stdout(stream):
            self.assertEqual(MODULE.main(["--manifest", "/root/manifest.json"]), 2)
        apply.assert_not_called()
        output = json.loads(stream.getvalue())
        self.assertEqual(output["status"], "blocked")

    def test_bridge_source_has_no_direct_file_transfer_client(self) -> None:
        source = Path(MODULE.__file__).read_text("ascii")
        for forbidden in ("scp", "rsync", "sftp"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
