import unittest

from api.routers.realtime import sanitize_payload


class RealtimeRouterSanitizeTests(unittest.TestCase):
    def test_sanitize_payload_strips_sensitive_fields(self):
        payload = {
            "mobile_number": "0912",
            "address": "addr",
            "telegram_id": 123,
            "idempotency_key": "x",
            "safe": 1,
        }

        self.assertEqual(sanitize_payload(payload), {"safe": 1})

    def test_sanitize_payload_returns_non_dict_values_unchanged(self):
        self.assertEqual(sanitize_payload("x"), "x")
        self.assertEqual(sanitize_payload([1, 2]), [1, 2])


if __name__ == "__main__":
    unittest.main()