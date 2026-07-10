import unittest
import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import IntegrityError

import main


class _AsyncSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class MainLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_can_disable_background_jobs_for_isolated_recovery(self):
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        redis_client = AsyncMock()

        with patch.object(main.settings, "server_mode", "iran"), patch.object(
            main.settings, "background_jobs_enabled", False
        ), patch("main.init_db", new=AsyncMock()), patch(
            "main.init_redis", new=AsyncMock(return_value=redis_client)
        ), patch("main.close_redis", new=AsyncMock()) as close_redis_mock, patch(
            "main.setup_event_listeners"
        ), patch("main.AsyncSessionLocal", return_value=_AsyncSessionContext(session)), patch(
            "main.ensure_mandatory_channel_rollout", new=AsyncMock()
        ), patch("main._start_background_leader_task") as leader_mock:
            async with main.lifespan(main.app):
                pass

        leader_mock.assert_not_called()
        close_redis_mock.assert_awaited_once()

    async def test_lifespan_starts_background_leader_and_closes_redis(self):
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        redis_client = AsyncMock()

        def start_leader(client):
            return asyncio.create_task(asyncio.sleep(3600))

        with patch.object(main.settings, "server_mode", "iran"), patch("main.init_db", new=AsyncMock()) as init_db_mock, patch(
            "main.init_redis", new=AsyncMock(return_value=redis_client)
        ) as init_redis_mock, patch("main.close_redis", new=AsyncMock()) as close_redis_mock, patch(
            "main.setup_event_listeners"
        ) as setup_mock, patch("main.AsyncSessionLocal", return_value=_AsyncSessionContext(session)), patch(
            "main.ensure_mandatory_channel_rollout", new=AsyncMock()
        ) as rollout_mock, patch("main._start_background_leader_task", side_effect=start_leader) as leader_mock:
            async with main.lifespan(main.app):
                pass

        init_db_mock.assert_awaited_once()
        init_redis_mock.assert_awaited_once()
        setup_mock.assert_called_once_with()
        rollout_mock.assert_awaited_once_with(session)
        session.commit.assert_awaited_once()
        close_redis_mock.assert_awaited_once()
        leader_mock.assert_called_once_with(redis_client)

    async def test_background_job_factories_include_connectivity_only_on_iran(self):
        with patch.object(main.settings, "server_mode", "foreign"), patch("main.init_db", new=AsyncMock()), patch(
            "main.init_redis", new=AsyncMock()
        ):
            foreign_jobs = [name for name, _ in main._background_job_factories()]
        with patch.object(main.settings, "server_mode", "iran"):
            iran_jobs = [name for name, _ in main._background_job_factories()]

        self.assertNotIn("connectivity_monitor", foreign_jobs)
        self.assertNotIn("user_account_status", foreign_jobs)
        self.assertNotIn("trade_webapp_delivery", foreign_jobs)
        self.assertIn("trade_telegram_delivery", foreign_jobs)
        self.assertIn("connectivity_monitor", iran_jobs)
        self.assertIn("market_schedule", foreign_jobs)
        self.assertIn("user_account_status", iran_jobs)
        self.assertIn("trade_webapp_delivery", iran_jobs)
        self.assertNotIn("trade_telegram_delivery", iran_jobs)

    async def test_background_leader_starts_jobs_and_releases_lock_on_cancel(self):
        class FakeRedis:
            def __init__(self):
                self.set_calls = []
                self.eval_calls = []

            async def set(self, *args, **kwargs):
                self.set_calls.append((args, kwargs))
                return True

            async def eval(self, *args):
                self.eval_calls.append(args)
                return 1

        redis_client = FakeRedis()
        job_started = asyncio.Event()

        async def demo_job():
            job_started.set()
            await asyncio.Event().wait()

        with patch.object(main.settings, "server_mode", "foreign"), patch(
            "main._background_job_factories", return_value=[("demo", demo_job)]
        ):
            task = asyncio.create_task(main._run_background_leader(redis_client))
            await asyncio.wait_for(job_started.wait(), timeout=1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(redis_client.set_calls[0][0][0], f"{main.BACKGROUND_LEADER_LOCK_KEY}:foreign")
        self.assertTrue(redis_client.set_calls[0][1]["nx"])
        self.assertTrue(any(call[0] == main.BACKGROUND_LEADER_RELEASE_SCRIPT for call in redis_client.eval_calls))

    async def test_background_leader_lock_key_is_scoped_by_server_mode(self):
        with patch.object(main.settings, "server_mode", "iran"):
            self.assertEqual(main._background_leader_lock_key(), f"{main.BACKGROUND_LEADER_LOCK_KEY}:iran")
        with patch.object(main.settings, "server_mode", "foreign"):
            self.assertEqual(main._background_leader_lock_key(), f"{main.BACKGROUND_LEADER_LOCK_KEY}:foreign")

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

    async def test_lifespan_continues_on_mandatory_channel_membership_race(self):
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        redis_client = AsyncMock()
        duplicate_error = IntegrityError(
            "stmt",
            {},
            Exception('duplicate key value violates unique constraint "ux_chat_members_active_membership"'),
        )

        def start_leader(client):
            return asyncio.create_task(asyncio.sleep(3600))

        with patch.object(main.settings, "server_mode", "iran"), patch("main.init_db", new=AsyncMock()), patch(
            "main.init_redis", new=AsyncMock(return_value=redis_client)
        ), patch("main.close_redis", new=AsyncMock()) as close_redis_mock, patch(
            "main.setup_event_listeners"
        ), patch("main.AsyncSessionLocal", return_value=_AsyncSessionContext(session)), patch(
            "main.ensure_mandatory_channel_rollout", new=AsyncMock(side_effect=duplicate_error)
        ), patch("main._start_background_leader_task", side_effect=start_leader) as leader_mock:
            async with main.lifespan(main.app):
                pass

        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()
        close_redis_mock.assert_awaited_once()
        leader_mock.assert_called_once_with(redis_client)

    async def test_main_module_logs_when_frontend_build_directory_is_missing(self):
        with patch("pathlib.Path.exists", return_value=False), patch("logging.getLogger") as get_logger:
            importlib.reload(main)

        get_logger.return_value.warning.assert_called_with("⚠️ Frontend build directory not found. Run 'npm run build' first.")
        importlib.reload(main)


if __name__ == "__main__":
    unittest.main()
