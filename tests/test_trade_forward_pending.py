"""Stage 7: forwarding-server ambiguous timeout retention (M18)."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks

from api.routers import trades
from core.trade_forward_pending import (
    AMBIGUOUS_FORWARD_PENDING_MESSAGE,
    ambiguous_forward_pending_response,
    clear_trade_forward_pending,
    get_trade_forward_pending,
    mark_trade_forward_pending,
    reconcile_trade_forward_pending,
)


class _FakeRedis:
    def __init__(self):
        self.store = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)
        return 1

    async def expire(self, key, _ttl):
        return key in self.store


class TradeForwardPendingHelperTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = _FakeRedis()
        self.redis_patcher = patch(
            "core.trade_forward_pending.get_redis_client",
            return_value=self.redis,
        )
        self.redis_patcher.start()
        self.server_patcher = patch(
            "core.trade_forward_pending.current_server",
            return_value="foreign",
        )
        self.server_patcher.start()

    async def asyncTearDown(self):
        self.server_patcher.stop()
        self.redis_patcher.stop()

    async def test_mark_get_clear_roundtrip(self):
        ok = await mark_trade_forward_pending(
            idempotency_key="idem-1",
            home_server="iran",
            payload={"offer_id": 7, "idempotency_key": "idem-1"},
        )
        self.assertTrue(ok)
        pending = await get_trade_forward_pending("idem-1")
        self.assertEqual(pending["home_server"], "iran")
        self.assertEqual(pending["payload"]["offer_id"], 7)
        await clear_trade_forward_pending("idem-1")
        self.assertIsNone(await get_trade_forward_pending("idem-1"))

    async def test_mark_without_key_is_skipped(self):
        self.assertFalse(
            await mark_trade_forward_pending(
                idempotency_key="",
                home_server="iran",
                payload={},
            )
        )

    async def test_reconcile_clears_on_definite_answer(self):
        await mark_trade_forward_pending(
            idempotency_key="idem-2",
            home_server="iran",
            payload={"offer_id": 7, "idempotency_key": "idem-2"},
        )
        with patch(
            "core.trade_forwarding.forward_trade_to_home_server",
            AsyncMock(return_value=(202, {"workflow": "overtime"})),
        ):
            result = await reconcile_trade_forward_pending("idem-2")
        self.assertEqual(result[0], 202)
        self.assertIsNone(await get_trade_forward_pending("idem-2"))

    async def test_reconcile_keeps_marker_on_timeout(self):
        await mark_trade_forward_pending(
            idempotency_key="idem-3",
            home_server="iran",
            payload={"offer_id": 7, "idempotency_key": "idem-3"},
        )
        with patch(
            "core.trade_forwarding.forward_trade_to_home_server",
            AsyncMock(return_value=(504, {"detail": "timeout"})),
        ):
            result = await reconcile_trade_forward_pending("idem-3")
        self.assertIsNone(result)
        self.assertIsNotNone(await get_trade_forward_pending("idem-3"))

    def test_m18_response_copy(self):
        payload = ambiguous_forward_pending_response(
            idempotency_key="idem-x",
            offer_public_id="ofr_1",
        )
        self.assertEqual(payload["detail"], AMBIGUOUS_FORWARD_PENDING_MESSAGE)
        self.assertEqual(payload["detail"], "⏳ در حال بررسی درخواست...")
        self.assertTrue(payload["pending"])


class ForwardTradeAmbiguousPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_forward_504_with_key_returns_m18_and_marks_pending(self):
        trade_data = SimpleNamespace(offer_id=7, quantity=4, idempotency_key="idem-504")
        current_user = SimpleNamespace(id=99)
        edge_received_at = __import__("datetime").datetime(2026, 8, 5, 12, 0)
        remote_offer = SimpleNamespace(home_server="iran", offer_public_id="ofr_remote")
        db = SimpleNamespace(get=AsyncMock(return_value=remote_offer))
        background = BackgroundTasks()
        mark = AsyncMock(return_value=True)

        with patch("api.routers.trades.is_remote_home", return_value=True), patch(
            "api.routers.trades.current_server", return_value="foreign"
        ), patch(
            "api.routers.trades.forward_trade_to_home_server",
            AsyncMock(return_value=(504, {"detail": "timeout"})),
        ), patch(
            "api.routers.trades.mark_trade_forward_pending",
            mark,
        ):
            response = await trades._forward_trade_if_remote_home(
                db,
                trade_data,
                current_user,
                edge_received_at,
                background_tasks=background,
            )

        self.assertEqual(response.status_code, 202)
        body = json.loads(response.body)
        self.assertEqual(body["detail"], AMBIGUOUS_FORWARD_PENDING_MESSAGE)
        self.assertEqual(body["workflow"], "forward_pending")
        mark.assert_awaited_once()
        self.assertEqual(len(background.tasks), 1)

    async def test_forward_503_does_not_retain_pending(self):
        trade_data = SimpleNamespace(offer_id=7, quantity=4, idempotency_key="idem-503")
        current_user = SimpleNamespace(id=99)
        edge_received_at = __import__("datetime").datetime(2026, 8, 5, 12, 0)
        remote_offer = SimpleNamespace(home_server="iran", offer_public_id="ofr_remote")
        db = SimpleNamespace(get=AsyncMock(return_value=remote_offer))
        mark = AsyncMock(return_value=True)

        with patch("api.routers.trades.is_remote_home", return_value=True), patch(
            "api.routers.trades.current_server", return_value="foreign"
        ), patch(
            "api.routers.trades.forward_trade_to_home_server",
            AsyncMock(return_value=(503, {"detail": "down"})),
        ), patch(
            "api.routers.trades.mark_trade_forward_pending",
            mark,
        ):
            response = await trades._forward_trade_if_remote_home(
                db,
                trade_data,
                current_user,
                edge_received_at,
            )

        self.assertEqual(response.status_code, 503)
        mark.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
