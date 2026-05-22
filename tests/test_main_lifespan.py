import unittest
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main


class _AsyncSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class MainLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_starts_expected_tasks_on_iran_and_closes_redis(self):
        created = []

        async def connectivity_monitor_loop():
            return None

        async def offer_expiry_loop():
            return None

        async def market_schedule_loop():
            return None

        async def session_expiry_loop():
            return None

        def fake_create_task(coro):
            name = getattr(getattr(coro, "cr_code", None), "co_name", "unknown")
            created.append(name)
            if hasattr(coro, "close"):
                coro.close()
            return SimpleNamespace()

        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

        with patch.object(main.settings, "server_mode", "iran"), patch("main.init_db", new=AsyncMock()) as init_db_mock, patch(
            "main.init_redis", new=AsyncMock()
        ) as init_redis_mock, patch("main.close_redis", new=AsyncMock()) as close_redis_mock, patch(
            "main.setup_event_listeners"
        ) as setup_mock, patch("main.AsyncSessionLocal", return_value=_AsyncSessionContext(session)), patch(
            "main.ensure_mandatory_channel_rollout", new=AsyncMock()
        ) as rollout_mock, patch("main.connectivity_monitor_loop", new=connectivity_monitor_loop), patch(
            "main.offer_expiry_loop", new=offer_expiry_loop
        ), patch("main.market_schedule_loop", new=market_schedule_loop), patch(
            "main.session_expiry_loop", new=session_expiry_loop
        ), patch("main.asyncio.create_task", side_effect=fake_create_task):
            async with main.lifespan(main.app):
                pass

        init_db_mock.assert_awaited_once()
        init_redis_mock.assert_awaited_once()
        setup_mock.assert_called_once_with()
        rollout_mock.assert_awaited_once_with(session)
        session.commit.assert_awaited_once()
        close_redis_mock.assert_awaited_once()
        self.assertEqual(len(created), 5)
        self.assertIn("market_schedule_loop", created)
        self.assertIn("user_account_status_loop", created)

    async def test_lifespan_skips_connectivity_monitor_outside_iran(self):
        created = []

        async def connectivity_monitor_loop():
            return None

        async def offer_expiry_loop():
            return None

        async def market_schedule_loop():
            return None

        async def session_expiry_loop():
            return None

        def fake_create_task(coro):
            name = getattr(getattr(coro, "cr_code", None), "co_name", "unknown")
            created.append(name)
            if hasattr(coro, "close"):
                coro.close()
            return SimpleNamespace()

        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

        with patch.object(main.settings, "server_mode", "foreign"), patch("main.init_db", new=AsyncMock()), patch(
            "main.init_redis", new=AsyncMock()
        ), patch("main.close_redis", new=AsyncMock()), patch("main.setup_event_listeners"), patch(
            "main.AsyncSessionLocal", return_value=_AsyncSessionContext(session)
        ), patch("main.ensure_mandatory_channel_rollout", new=AsyncMock()) as rollout_mock, patch(
            "main.connectivity_monitor_loop", new=connectivity_monitor_loop
        ), patch("main.offer_expiry_loop", new=offer_expiry_loop), patch(
            "main.market_schedule_loop", new=market_schedule_loop
        ), patch(
            "main.session_expiry_loop", new=session_expiry_loop
        ), patch("main.asyncio.create_task", side_effect=fake_create_task):
            async with main.lifespan(main.app):
                pass

        rollout_mock.assert_awaited_once_with(session)
        session.commit.assert_awaited_once()
        self.assertEqual(len(created), 4)
        self.assertIn("market_schedule_loop", created)
        self.assertIn("user_account_status_loop", created)

    async def test_lifespan_rolls_back_mandatory_rollout_failures(self):
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

        with patch.object(main.settings, "server_mode", "foreign"), patch("main.init_db", new=AsyncMock()), patch(
            "main.init_redis", new=AsyncMock()
        ), patch("main.close_redis", new=AsyncMock()), patch("main.setup_event_listeners"), patch(
            "main.AsyncSessionLocal", return_value=_AsyncSessionContext(session)
        ), patch(
            "main.ensure_mandatory_channel_rollout", new=AsyncMock(side_effect=RuntimeError("rollout failed"))
        ), patch("main.asyncio.create_task"):
            with self.assertRaises(RuntimeError):
                async with main.lifespan(main.app):
                    pass

        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()

    async def test_main_module_logs_when_frontend_build_directory_is_missing(self):
        with patch("pathlib.Path.exists", return_value=False), patch("logging.getLogger") as get_logger:
            importlib.reload(main)

        get_logger.return_value.warning.assert_called_with("⚠️ Frontend build directory not found. Run 'npm run build' first.")
        importlib.reload(main)


if __name__ == "__main__":
    unittest.main()