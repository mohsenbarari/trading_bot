import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from core import connectivity


class CoreConnectivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_connectivity_uses_foreign_url_for_iran(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=SimpleNamespace(status_code=200))
        context_manager = AsyncMock()
        context_manager.__aenter__.return_value = client
        context_manager.__aexit__.return_value = False

        with patch.object(connectivity.settings, 'server_mode', 'iran'), patch.object(
            connectivity.settings, 'foreign_server_url', 'https://foreign.example'
        ), patch('core.connectivity.httpx.AsyncClient', return_value=context_manager):
            result = await connectivity.check_connectivity()

        self.assertTrue(result)
        client.get.assert_awaited_once_with('https://foreign.example')

    async def test_check_connectivity_never_falls_back_to_telegram_on_iran_without_peer_url(self):
        with patch.object(connectivity.settings, 'server_mode', 'iran'), patch.object(
            connectivity.settings, 'foreign_server_url', None
        ), patch.object(connectivity.settings, 'peer_server_url', None), patch.object(
            connectivity.settings, 'germany_server_url', None
        ), patch('core.connectivity.httpx.AsyncClient') as client_ctor, patch.object(connectivity, 'logger') as logger:
            result = await connectivity.check_connectivity()

        self.assertFalse(result)
        client_ctor.assert_not_called()
        logger.warning.assert_called_once()

    async def test_check_connectivity_returns_false_on_timeout(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectTimeout('boom'))
        context_manager = AsyncMock()
        context_manager.__aenter__.return_value = client
        context_manager.__aexit__.return_value = False

        with patch.object(connectivity.settings, 'server_mode', 'foreign'), patch(
            'core.connectivity.httpx.AsyncClient', return_value=context_manager
        ):
            result = await connectivity.check_connectivity()

        self.assertFalse(result)

    async def test_check_connectivity_logs_unexpected_errors(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=RuntimeError('boom'))
        context_manager = AsyncMock()
        context_manager.__aenter__.return_value = client
        context_manager.__aexit__.return_value = False

        with patch.object(connectivity.settings, 'server_mode', 'foreign'), patch(
            'core.connectivity.httpx.AsyncClient', return_value=context_manager
        ), patch.object(connectivity, 'logger') as logger:
            result = await connectivity.check_connectivity()

        self.assertFalse(result)
        logger.debug.assert_called_once()

    async def test_connectivity_monitor_loop_writes_status_then_waits(self):
        redis_client = AsyncMock()

        async def stop_after_first_sleep(_delay):
            raise asyncio.CancelledError()

        with patch.object(connectivity.settings, 'server_mode', 'iran'), patch(
            'core.connectivity.get_redis', AsyncMock(return_value=redis_client)
        ), patch('core.connectivity.check_connectivity', AsyncMock(return_value=True)), patch(
            'core.connectivity.asyncio.sleep', side_effect=stop_after_first_sleep
        ):
            with self.assertRaises(asyncio.CancelledError):
                await connectivity.connectivity_monitor_loop()

        redis_client.set.assert_awaited_once_with(connectivity.REDIS_KEY_CONNECTIVITY, 'true')

    async def test_connectivity_monitor_loop_returns_immediately_outside_iran(self):
        with patch.object(connectivity.settings, 'server_mode', 'foreign'), patch(
            'core.connectivity.get_redis', AsyncMock()
        ) as get_redis:
            await connectivity.connectivity_monitor_loop()

        get_redis.assert_not_awaited()

    async def test_is_internet_connected_short_circuits_outside_iran(self):
        with patch.object(connectivity.settings, 'server_mode', 'foreign'):
            self.assertTrue(await connectivity.is_internet_connected())

    async def test_is_internet_connected_reads_cached_redis_value(self):
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(return_value='true')

        with patch.object(connectivity.settings, 'server_mode', 'iran'), patch(
            'core.connectivity.get_redis', AsyncMock(return_value=redis_client)
        ):
            self.assertTrue(await connectivity.is_internet_connected())

    async def test_is_internet_connected_returns_false_for_cached_false(self):
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(return_value='false')

        with patch.object(connectivity.settings, 'server_mode', 'iran'), patch(
            'core.connectivity.get_redis', AsyncMock(return_value=redis_client)
        ):
            self.assertFalse(await connectivity.is_internet_connected())


if __name__ == '__main__':
    unittest.main()
