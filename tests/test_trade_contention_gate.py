import unittest
from unittest.mock import patch

from core.services import trade_contention_gate as gate


class FakeRedis:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.hashes = {}
        self.closed = False

    async def eval(self, script, _num_keys, key, *args):
        if self.fail:
            raise RuntimeError("redis unavailable")
        if "hset" in script.lower():
            token, _ttl_ms, max_inflight = args
            slots = self.hashes.setdefault(key, set())
            if len(slots) >= int(max_inflight):
                return 0
            slots.add(token)
            return 1
        token = args[0]
        slots = self.hashes.get(key, set())
        removed = 1 if token in slots else 0
        slots.discard(token)
        if not slots:
            self.hashes.pop(key, None)
        return removed

    async def aclose(self):
        self.closed = True


class TradeContentionGateTests(unittest.IsolatedAsyncioTestCase):
    def test_gate_key_hashes_offer_identity(self):
        public_key = gate.build_trade_contention_gate_key(offer_public_id="ofr_public_1")
        legacy_key = gate.build_trade_contention_gate_key(offer_id=42)

        self.assertTrue(public_key.startswith("trade:contention:"))
        self.assertTrue(legacy_key.startswith("trade:contention:"))
        self.assertNotIn("ofr_public_1", public_key)
        self.assertNotIn("42", legacy_key)
        self.assertNotEqual(public_key, legacy_key)

    async def test_gate_acquire_rejects_above_inflight_limit_and_owner_release(self):
        redis = FakeRedis()
        with patch("core.services.trade_contention_gate._get_gate_client", return_value=(redis, False)):
            first = await gate.try_acquire_trade_contention_gate(
                offer_public_id="ofr_busy",
                ttl_seconds=2.5,
                max_inflight=2,
            )
            second = await gate.try_acquire_trade_contention_gate(
                offer_public_id="ofr_busy",
                ttl_seconds=2.5,
                max_inflight=2,
            )
            third = await gate.try_acquire_trade_contention_gate(
                offer_public_id="ofr_busy",
                ttl_seconds=2.5,
                max_inflight=2,
            )

            self.assertTrue(first.acquired)
            self.assertTrue(second.acquired)
            self.assertFalse(third.acquired)

            await first.release()
            replacement = await gate.try_acquire_trade_contention_gate(
                offer_public_id="ofr_busy",
                ttl_seconds=2.5,
                max_inflight=2,
            )
            self.assertTrue(replacement.acquired)

    async def test_gate_allows_request_when_redis_is_unavailable(self):
        redis = FakeRedis(fail=True)
        with patch("core.services.trade_contention_gate._get_gate_client", return_value=(redis, False)):
            lease = await gate.try_acquire_trade_contention_gate(offer_public_id="ofr_fallback", ttl_seconds=2.5)

        self.assertTrue(lease.acquired)


if __name__ == "__main__":
    unittest.main()
