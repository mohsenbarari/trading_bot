from __future__ import annotations

import copy
import unittest

from core.dedicated_host_preflight_aggregate import (
    OBSERVATION_AGGREGATE_STATUS,
    PREFLIGHT_AGGREGATE_SCHEMA,
    PREFLIGHT_MANIFEST_BINDING_SCHEMA,
    READINESS_DECISION_NOT_EVALUATED,
    ROLE_ORDER,
    DedicatedHostPreflightAggregateError,
    preflight_aggregate_sha256,
    validate_preflight_aggregate,
    validate_validated_manifest_binding,
)
from tests.test_dedicated_host_preflight_receipt import (
    CAMPAIGN_ID,
    MANIFEST_SHA256,
    OPERATION_ID,
    RELEASE_SHA,
    valid_receipt,
)


ROLE_INSTANCES = {
    "bot_fi": ("baf42d90-4f4d-4bb7-8d2c-fec3c11bcb9e", "8.8.8.8"),
    "webapp_fi": ("01e4a6e2-78a4-4a6a-a7f5-1a7791a16641", "1.1.1.1"),
    "webapp_ir": ("de4ce67a-8f32-4bb2-bf04-7cbfa10a8cda", "9.9.9.9"),
    "witness": ("cf614b3e-8828-45ae-9691-60780da74a34", "208.67.222.222"),
}


def validated_manifest() -> dict:
    return {
        "schema": PREFLIGHT_MANIFEST_BINDING_SCHEMA,
        "status": "validated",
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "manifest_sha256": MANIFEST_SHA256,
        "roles": [
            {
                "role": role,
                "instance_id": ROLE_INSTANCES[role][0],
                "public_ipv4": ROLE_INSTANCES[role][1],
            }
            for role in ROLE_ORDER
        ],
    }


def receipts() -> list[dict]:
    result = []
    for role in ROLE_ORDER:
        receipt = valid_receipt()
        receipt["role"] = role
        receipt["observation"]["role_marker"] = role
        receipt["instance"]["server_id"] = ROLE_INSTANCES[role][0]
        receipt["instance"]["public_ipv4"] = ROLE_INSTANCES[role][1]
        result.append(receipt)
    return result


class DedicatedHostPreflightAggregateTests(unittest.TestCase):
    def test_aggregate_requires_all_four_ordered_role_receipts(self):
        aggregate = validate_preflight_aggregate(validated_manifest(), receipts())

        self.assertEqual(aggregate["schema"], PREFLIGHT_AGGREGATE_SCHEMA)
        self.assertEqual(aggregate["status"], OBSERVATION_AGGREGATE_STATUS)
        self.assertEqual(aggregate["decision"], READINESS_DECISION_NOT_EVALUATED)
        self.assertNotIn("ready", aggregate["decision"])
        self.assertEqual([item["role"] for item in aggregate["receipts"]], list(ROLE_ORDER))
        self.assertEqual(
            preflight_aggregate_sha256(validated_manifest(), receipts()),
            preflight_aggregate_sha256(copy.deepcopy(validated_manifest()), copy.deepcopy(receipts())),
        )

    def test_missing_duplicate_and_out_of_order_receipts_are_rejected(self):
        with self.assertRaises(DedicatedHostPreflightAggregateError):
            validate_preflight_aggregate(validated_manifest(), receipts()[:3])

        duplicate = receipts()
        duplicate[-1] = copy.deepcopy(duplicate[-2])
        with self.assertRaises(DedicatedHostPreflightAggregateError):
            validate_preflight_aggregate(validated_manifest(), duplicate)

        reversed_receipts = list(reversed(receipts()))
        with self.assertRaises(DedicatedHostPreflightAggregateError):
            validate_preflight_aggregate(validated_manifest(), reversed_receipts)

    def test_campaign_release_manifest_instance_and_ip_mismatches_are_rejected(self):
        checks = []
        changed_campaign = receipts()
        changed_campaign[0]["campaign_id"] = "full-matrix-destructive-20260731"
        checks.append(changed_campaign)

        changed_operation = receipts()
        changed_operation[0]["operation_id"] = "f6d5dabe-9c52-4517-b6de-7ebbc55355ca"
        checks.append(changed_operation)

        changed_release = receipts()
        changed_release[0]["release_sha"] = "f" * 40
        checks.append(changed_release)

        changed_manifest = receipts()
        changed_manifest[0]["manifest_sha256"] = "b" * 64
        checks.append(changed_manifest)

        changed_instance = receipts()
        changed_instance[0]["instance"]["server_id"] = "11111111-2222-4333-8444-555555555555"
        checks.append(changed_instance)

        changed_ip = receipts()
        changed_ip[0]["instance"]["public_ipv4"] = "8.8.4.4"
        checks.append(changed_ip)

        for receipt_set in checks:
            with self.subTest(receipt=receipt_set[0]), self.assertRaises(DedicatedHostPreflightAggregateError):
                validate_preflight_aggregate(validated_manifest(), receipt_set)

    def test_manifest_role_order_duplicate_and_missing_roles_are_rejected(self):
        duplicate = validated_manifest()
        duplicate["roles"][-1] = copy.deepcopy(duplicate["roles"][-2])
        with self.assertRaises(DedicatedHostPreflightAggregateError):
            validate_validated_manifest_binding(duplicate)

        out_of_order = validated_manifest()
        out_of_order["roles"][0], out_of_order["roles"][1] = (
            out_of_order["roles"][1],
            out_of_order["roles"][0],
        )
        with self.assertRaises(DedicatedHostPreflightAggregateError):
            validate_validated_manifest_binding(out_of_order)

        duplicated_instance = validated_manifest()
        duplicated_instance["roles"][-1]["instance_id"] = duplicated_instance["roles"][0]["instance_id"]
        with self.assertRaises(DedicatedHostPreflightAggregateError):
            validate_validated_manifest_binding(duplicated_instance)

    def test_manifest_rejects_capability_fields_urls_and_secret_shaped_content(self):
        capability = validated_manifest()
        capability["action"] = "power-off"
        with self.assertRaises(DedicatedHostPreflightAggregateError):
            validate_validated_manifest_binding(capability)

        secret = validated_manifest()
        secret["campaign_id"] = "secret-destructive-campaign"
        with self.assertRaises(DedicatedHostPreflightAggregateError):
            validate_validated_manifest_binding(secret)

        url = validated_manifest()
        url["roles"][0]["public_ipv4"] = "https://not-a-host.example"
        with self.assertRaises(DedicatedHostPreflightAggregateError):
            validate_validated_manifest_binding(url)


if __name__ == "__main__":
    unittest.main()
