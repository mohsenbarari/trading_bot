from __future__ import annotations

import copy
import json
import unittest

from core.dedicated_host_preflight_receipt import (
    DedicatedHostPreflightReceiptError,
    PREFLIGHT_RECEIPT_SCHEMA,
    canonical_json_bytes,
    parse_preflight_receipt,
    preflight_receipt_sha256,
    validate_preflight_receipt,
)


CAMPAIGN_ID = "full-matrix-destructive-20260730"
OPERATION_ID = "f6d5dabe-9c52-4517-b6de-7ebbc55355c9"
RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
MANIFEST_SHA256 = "a" * 64
INSTANCE_ID = "baf42d90-4f4d-4bb7-8d2c-fec3c11bcb9e"


def valid_receipt() -> dict:
    return {
        "schema": PREFLIGHT_RECEIPT_SCHEMA,
        "status": "observed",
        "observation_mode": "read-only",
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "role": "bot_fi",
        "instance": {
            "provider": "arvan_ecc",
            "server_id": INSTANCE_ID,
            "public_ipv4": "8.8.8.8",
        },
        "manifest_sha256": MANIFEST_SHA256,
        "observed_at": "2026-07-30T16:00:00Z",
        "observation": {
            "role_marker": "bot_fi",
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


class DedicatedHostPreflightReceiptTests(unittest.TestCase):
    def test_valid_receipt_binds_all_expected_identities(self):
        receipt = valid_receipt()

        actual = validate_preflight_receipt(
            receipt,
            expected_role="bot_fi",
            expected_campaign_id=CAMPAIGN_ID,
            expected_operation_id=OPERATION_ID,
            expected_instance_id=INSTANCE_ID,
            expected_manifest_sha256=MANIFEST_SHA256,
        )

        self.assertEqual(actual, receipt)
        self.assertEqual(preflight_receipt_sha256(receipt), preflight_receipt_sha256(actual))

    def test_binding_mismatch_is_rejected_for_every_identity(self):
        receipt = valid_receipt()
        checks = (
            {"expected_role": "webapp_fi"},
            {"expected_campaign_id": "full-matrix-destructive-20260731"},
            {"expected_operation_id": "f6d5dabe-9c52-4517-b6de-7ebbc55355ca"},
            {"expected_instance_id": "baf42d90-4f4d-4bb7-8d2c-fec3c11bcb9f"},
            {"expected_manifest_sha256": "b" * 64},
        )

        for expected in checks:
            with self.subTest(expected=expected), self.assertRaises(DedicatedHostPreflightReceiptError):
                validate_preflight_receipt(receipt, **expected)

    def test_role_marker_and_release_binding_cannot_drift(self):
        marker_drift = valid_receipt()
        marker_drift["observation"]["role_marker"] = "witness"
        with self.assertRaises(DedicatedHostPreflightReceiptError):
            validate_preflight_receipt(marker_drift)

        release_drift = valid_receipt()
        release_drift["observation"]["release"]["release_sha"] = "f" * 40
        with self.assertRaises(DedicatedHostPreflightReceiptError):
            validate_preflight_receipt(release_drift)

    def test_schema_has_no_execution_mode_or_capability_fields(self):
        receipt = valid_receipt()
        receipt["command"] = "power-off"
        with self.assertRaises(DedicatedHostPreflightReceiptError):
            validate_preflight_receipt(receipt)

        receipt = valid_receipt()
        receipt["observation_mode"] = "execute"
        with self.assertRaises(DedicatedHostPreflightReceiptError):
            validate_preflight_receipt(receipt)

    def test_urls_and_secret_shaped_content_are_rejected_even_in_known_fields(self):
        receipt = valid_receipt()
        receipt["campaign_id"] = "https://controller.example/receipt"
        with self.assertRaises(DedicatedHostPreflightReceiptError):
            validate_preflight_receipt(receipt)

        receipt = valid_receipt()
        receipt["instance"]["provider"] = "arvan-secret"
        with self.assertRaises(DedicatedHostPreflightReceiptError):
            validate_preflight_receipt(receipt)

        receipt = valid_receipt()
        receipt["observation"]["runtime"]["token"] = "not-allowed"
        with self.assertRaises(DedicatedHostPreflightReceiptError):
            validate_preflight_receipt(receipt)

    def test_missing_release_carries_no_claimed_release_state(self):
        receipt = valid_receipt()
        receipt["role"] = "webapp_fi"
        receipt["observation"]["role_marker"] = "webapp_fi"
        receipt["observation"]["release"] = {
            "state": "missing",
            "release_sha": None,
            "clean": None,
        }

        actual = validate_preflight_receipt(receipt)

        self.assertEqual(actual["observation"]["release"]["state"], "missing")

    def test_strict_json_parser_requires_canonical_bytes_and_preserves_digest(self):
        raw = canonical_json_bytes(valid_receipt()) + b"\n"
        parsed = parse_preflight_receipt(raw)
        reordered = copy.deepcopy(valid_receipt())
        self.assertEqual(preflight_receipt_sha256(parsed), preflight_receipt_sha256(reordered))

        with self.assertRaises(DedicatedHostPreflightReceiptError):
            parse_preflight_receipt(b'{"schema":"a","schema":"b"}\n')

        with self.assertRaisesRegex(DedicatedHostPreflightReceiptError, "canonical"):
            parse_preflight_receipt(json.dumps(valid_receipt(), sort_keys=True).encode("ascii") + b"\n")

        with self.assertRaisesRegex(DedicatedHostPreflightReceiptError, "canonical"):
            parse_preflight_receipt(canonical_json_bytes(valid_receipt()))

        with self.assertRaisesRegex(DedicatedHostPreflightReceiptError, "bytes"):
            parse_preflight_receipt(canonical_json_bytes(valid_receipt()).decode("ascii"))


if __name__ == "__main__":
    unittest.main()
