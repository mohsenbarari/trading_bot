import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main


class MainLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_starts_expected_tasks_on_iran_and_closes_redis(self):
        created = []

        def fake_create_task(coro):
            name = getattr(getattr(coro, "cr_code", None), "co_name", "unknown")
            created.append(name)
            if hasattr(coro, "close"):
                coro.close()
            return SimpleNamespace()

        with patch.object(main.settings, "server_mode", "iran"), patch("main.init_db", new=AsyncMock()) as init_db_mock, patch(
            "main.init_redis", new=AsyncMock()
        ) as init_redis_mock, patch("main.close_redis", new=AsyncMock()) as close_redis_mock, patch(
            "main.setup_event_listeners"
        ) as setup_mock, patch("main.connectivity_monitor_loop", new=AsyncMock()), patch(
            "main.offer_expiry_loop", new=AsyncMock()
        ), patch("main.session_expiry_loop", new=AsyncMock()), patch("main.asyncio.create_task", side_effect=fake_create_task):
            async with main.lifespan(main.app):
                pass

        init_db_mock.assert_awaited_once()
        init_redis_mock.assert_awaited_once()
        setup_mock.assert_called_once_with()
        close_redis_mock.assert_awaited_once()
        self.assertEqual(len(created), 3)

    async def test_lifespan_skips_connectivity_monitor_outside_iran(self):
        created = []

        def fake_create_task(coro):
            name = getattr(getattr(coro, "cr_code", None), "co_name", "unknown")
            created.append(name)
            if hasattr(coro, "close"):
                coro.close()
            return SimpleNamespace()

        with patch.object(main.settings, "server_mode", "foreign"), patch("main.init_db", new=AsyncMock()), patch(
            "main.init_redis", new=AsyncMock()
        ), patch("main.close_redis", new=AsyncMock()), patch("main.setup_event_listeners"), patch(
            "main.connectivity_monitor_loop", new=AsyncMock()
        ), patch("main.offer_expiry_loop", new=AsyncMock()), patch(
            "main.session_expiry_loop", new=AsyncMock()
        ), patch("main.asyncio.create_task", side_effect=fake_create_task):
            async with main.lifespan(main.app):
                pass

        self.assertEqual(len(created), 2)


if __name__ == "__main__":
    unittest.main()