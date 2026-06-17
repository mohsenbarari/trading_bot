import unittest
from unittest.mock import AsyncMock, patch

from core import market_presence
from api.routers import realtime


class FakeRedis:
    def __init__(self, keys_by_pattern=None):
        self.keys_by_pattern = keys_by_pattern or {}
        self.setex = AsyncMock()
        self.delete = AsyncMock()
        self.expire = AsyncMock()

    async def scan_iter(self, *, match, count=10):
        for key in self.keys_by_pattern.get(match, []):
            yield key


class MarketPresenceTests(unittest.IsolatedAsyncioTestCase):
    def test_is_market_route_normalizes_query_hash_and_trailing_slash(self):
        self.assertTrue(market_presence.is_market_route("/market"))
        self.assertTrue(market_presence.is_market_route("/market?tab=all"))
        self.assertTrue(market_presence.is_market_route("/market/#top"))
        self.assertFalse(market_presence.is_market_route("/notifications"))
        self.assertFalse(market_presence.is_market_route(None))

    async def test_set_refresh_and_clear_market_page_presence(self):
        redis = FakeRedis()

        with patch("core.market_presence.get_redis_client", return_value=redis):
            await market_presence.set_market_page_presence(7, "conn-1", path="/market", visible=True)
            await market_presence.refresh_market_page_presence(7, "conn-1", active=True)
            await market_presence.set_market_page_presence(7, "conn-1", path="/market", visible=False)
            await market_presence.clear_market_page_presence(7, "conn-1")

        key = "presence:market_page:7:conn-1"
        redis.setex.assert_awaited_once_with(key, market_presence.MARKET_PAGE_PRESENCE_TTL_SECONDS, "1")
        redis.expire.assert_awaited_once_with(key, market_presence.MARKET_PAGE_PRESENCE_TTL_SECONDS)
        self.assertEqual(redis.delete.await_count, 2)

    async def test_load_market_page_user_ids_returns_users_with_any_connection(self):
        redis = FakeRedis(
            {
                "presence:market_page:2:*": ["presence:market_page:2:conn-a"],
                "presence:market_page:3:*": [],
            }
        )

        with patch("core.market_presence.get_redis_client", return_value=redis):
            result = await market_presence.load_market_page_user_ids([2, 3])

        self.assertEqual(result, {2})

    async def test_realtime_client_message_updates_and_refreshes_presence(self):
        with patch("api.routers.realtime.set_market_page_presence", new=AsyncMock()) as set_presence, patch(
            "api.routers.realtime.refresh_market_page_presence",
            new=AsyncMock(),
        ) as refresh_presence:
            active = await realtime._handle_client_message(
                '{"type":"presence:update","data":{"path":"/market","visible":true}}',
                user_id=9,
                connection_id="conn-9",
                market_page_presence_active=False,
            )
            refreshed = await realtime._handle_client_message(
                "ping",
                user_id=9,
                connection_id="conn-9",
                market_page_presence_active=active,
            )

        self.assertTrue(active)
        self.assertTrue(refreshed)
        set_presence.assert_awaited_once_with(9, "conn-9", path="/market", visible=True)
        refresh_presence.assert_awaited_once_with(9, "conn-9", active=True)


if __name__ == "__main__":
    unittest.main()
