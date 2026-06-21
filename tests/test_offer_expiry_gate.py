import unittest
from unittest.mock import patch

from core.services import offer_expiry_gate as gate


class FakeRedis:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.values = {}
        self.closed = False

    async def set(self, key, value, *, nx=False, px=None):
        if self.fail:
            raise RuntimeError("redis unavailable")
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script, _num_keys, key, token):
        if self.values.get(key) == token:
            self.values.pop(key, None)
            return 1
        return 0

    async def aclose(self):
        self.closed = True


class OfferExpiryGateTests(unittest.IsolatedAsyncioTestCase):
    def test_gate_key_hashes_offer_id(self):
        key = gate.build_offer_expiry_gate_key(42)

        self.assertTrue(key.startswith("offer:expiry:"))
        self.assertNotIn("42", key)

    async def test_gate_rejects_second_inflight_and_releases(self):
        redis = FakeRedis()
        with patch("core.services.offer_expiry_gate._get_gate_client", return_value=(redis, False)):
            first = await gate.try_acquire_offer_expiry_gate(offer_id=42)
            second = await gate.try_acquire_offer_expiry_gate(offer_id=42)

            self.assertTrue(first.acquired)
            self.assertFalse(second.acquired)

            await first.release()
            replacement = await gate.try_acquire_offer_expiry_gate(offer_id=42)

        self.assertTrue(replacement.acquired)

    async def test_gate_fails_open_when_redis_unavailable(self):
        redis = FakeRedis(fail=True)
        with patch("core.services.offer_expiry_gate._get_gate_client", return_value=(redis, False)):
            lease = await gate.try_acquire_offer_expiry_gate(offer_id=42)

        self.assertTrue(lease.acquired)
        self.assertIsNone(lease.token)


if __name__ == "__main__":
    unittest.main()
