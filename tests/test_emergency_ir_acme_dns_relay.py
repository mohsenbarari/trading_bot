from __future__ import annotations

import importlib.util
from pathlib import Path
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "emergency_ir_acme_dns_relay",
    ROOT / "scripts" / "emergency_ir_acme_dns_relay.py",
)
assert SPEC and SPEC.loader
relay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay)


class EmergencyIrAcmeDnsRelayTests(unittest.TestCase):
    def descriptor(self) -> dict[str, object]:
        return {
            "schema": relay.SCHEMA,
            "operation_id": "a" * 32,
            "domain": relay.DOMAIN,
            "target_fqdn": relay.TARGET_FQDN,
            "deadline_epoch": int(time.time()) + 120,
            "request_put_url": "https://s3.ir-thr-at1.arvanstorage.ir/bucket/request?sig=one",
            "response_get_url": "https://s3.ir-thr-at1.arvanstorage.ir/bucket/response?sig=two",
        }

    def request(self, action: str = "present") -> dict[str, str]:
        return {
            "schema": relay.SCHEMA,
            "operation_id": "a" * 32,
            "action": action,
            "fqdn": relay.TARGET_FQDN,
            "value": "A" * 43,
            "request_id": "b" * 32,
        }

    def test_descriptor_rejects_foreign_object_storage_url(self):
        value = self.descriptor()
        value["request_put_url"] = "https://evil.example/request?sig=one"
        with self.assertRaisesRegex(relay.RelayError, "approved presigned"):
            relay._validate_descriptor(value)

    def test_descriptor_rejects_expired_or_overlong_deadline(self):
        expired = self.descriptor()
        expired["deadline_epoch"] = int(time.time()) - 1
        with self.assertRaisesRegex(relay.RelayError, "deadline"):
            relay._validate_descriptor(expired)
        excessive = self.descriptor()
        excessive["deadline_epoch"] = int(time.time()) + relay.MAX_OPERATION_SECONDS + 61
        with self.assertRaisesRegex(relay.RelayError, "deadline"):
            relay._validate_descriptor(excessive)

    def test_request_allows_only_exact_acme_name_and_action(self):
        descriptor = relay._validate_descriptor(self.descriptor())
        self.assertEqual(relay._validate_request(self.request(), descriptor)["fqdn"], relay.TARGET_FQDN)
        invalid = self.request()
        invalid["fqdn"] = "_acme-challenge.gold-trade.ir."
        with self.assertRaisesRegex(relay.RelayError, "allowlist"):
            relay._validate_request(invalid, descriptor)
        invalid = self.request("delete")
        with self.assertRaisesRegex(relay.RelayError, "action"):
            relay._validate_request(invalid, descriptor)

    def test_txt_payload_is_exact_and_non_proxied(self):
        captured: dict[str, object] = {}
        original = relay._arvan_request
        try:
            def fake(token: str, method: str, path: str, payload=None):
                captured.update(token=token, method=method, path=path, payload=payload)
                return {"data": {"id": "1a2b3c4d-1234-5678-9abc-def012345678"}}
            relay._arvan_request = fake
            record_id = relay._create_record("token", "A" * 43)
        finally:
            relay._arvan_request = original
        self.assertEqual(record_id, "1a2b3c4d-1234-5678-9abc-def012345678")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/domains/gold-trade.ir/dns-records")
        payload = captured["payload"]
        self.assertEqual(payload["type"], "txt")
        self.assertEqual(payload["name"], relay.TARGET_RECORD_NAME)
        self.assertFalse(payload["cloud"])
        self.assertEqual(payload["ttl"], relay.TTL)

    def test_cleanup_id_validation_is_strict(self):
        with self.assertRaisesRegex(relay.RelayError, "record id"):
            relay._delete_record("token", "../../wrong")


if __name__ == "__main__":
    unittest.main()
