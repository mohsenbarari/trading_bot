import unittest

from scripts.arvan_staging_dns01_exec import (
    ArvanDNS01Error,
    STAGING_FQDN,
    manage_dns01,
)


class FakeArvanDNS:
    def __init__(self, records=None):
        self.records = list(records or [])
        self.calls = []

    def __call__(self, method, url, token, payload):
        self.calls.append((method, url, payload))
        if token != "token":
            raise AssertionError("unexpected token")
        if method == "GET":
            return {"data": list(self.records)}
        if method == "POST":
            record = {"id": "created-id", **payload}
            self.records.append(record)
            return {"data": record}
        if method == "DELETE":
            record_id = url.rsplit("/", 1)[-1]
            self.records = [row for row in self.records if row["id"] != record_id]
            return {"data": {"id": record_id}}
        raise AssertionError(method)


class ArvanStagingDNS01ExecTests(unittest.TestCase):
    value = "A" * 43

    def test_present_creates_only_exact_txt_record(self):
        fake = FakeArvanDNS()
        result = manage_dns01(
            action="present", fqdn=STAGING_FQDN, value=self.value, token="token", request_fn=fake
        )
        self.assertEqual(result, {"status": "created", "record_count": 1})
        self.assertEqual([call[0] for call in fake.calls], ["GET", "POST"])
        payload = fake.calls[1][2]
        self.assertEqual(payload["type"], "txt")
        self.assertEqual(payload["name"], "_acme-challenge.staging")
        self.assertEqual(payload["value"], {"text": self.value})
        self.assertEqual(payload["ttl"], 600)

    def test_present_is_idempotent_for_exact_existing_value(self):
        fake = FakeArvanDNS(
            [{"id": "existing", "type": "txt", "name": "_acme-challenge.staging", "value": {"text": self.value}}]
        )
        result = manage_dns01(
            action="present", fqdn=STAGING_FQDN, value=self.value, token="token", request_fn=fake
        )
        self.assertEqual(result, {"status": "already_present", "record_count": 1})
        self.assertEqual([call[0] for call in fake.calls], ["GET"])

    def test_cleanup_removes_only_records_matching_exact_value(self):
        fake = FakeArvanDNS(
            [
                {"id": "remove", "type": "txt", "name": "_acme-challenge.staging", "value": {"text": self.value}},
                {"id": "keep", "type": "txt", "name": "_acme-challenge.staging", "value": {"text": "B" * 43}},
                {"id": "other", "type": "txt", "name": "unrelated", "value": {"text": self.value}},
            ]
        )
        result = manage_dns01(
            action="cleanup", fqdn=STAGING_FQDN, value=self.value, token="token", request_fn=fake
        )
        self.assertEqual(result, {"status": "cleaned", "record_count": 1})
        self.assertEqual([call[0] for call in fake.calls], ["GET", "DELETE"])
        self.assertEqual({row["id"] for row in fake.records}, {"keep", "other"})

    def test_rejects_other_fqdn_before_api_access(self):
        fake = FakeArvanDNS()
        with self.assertRaisesRegex(ArvanDNS01Error, "exact staging"):
            manage_dns01(
                action="present",
                fqdn="_acme-challenge.coin.gold-trade.ir.",
                value=self.value,
                token="token",
                request_fn=fake,
            )
        self.assertEqual(fake.calls, [])

    def test_rejects_invalid_action_and_value_before_api_access(self):
        fake = FakeArvanDNS()
        with self.assertRaisesRegex(ArvanDNS01Error, "present or cleanup"):
            manage_dns01(
                action="delete", fqdn=STAGING_FQDN, value=self.value, token="token", request_fn=fake
            )
        with self.assertRaisesRegex(ArvanDNS01Error, "unexpected format"):
            manage_dns01(
                action="present", fqdn=STAGING_FQDN, value="bad value", token="token", request_fn=fake
            )
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
