import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core import session_expiry


class _AsyncSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _ExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _ScalarResult(self._items)


class CoreSessionExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def test_expire_stale_sessions_returns_zero_when_nothing_matches(self):
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_ExecuteResult([]))
        with patch.object(session_expiry, 'AsyncSessionLocal', return_value=_AsyncSessionContext(session)):
            result = await session_expiry.expire_stale_sessions()

        self.assertEqual(result, 0)
        session.commit.assert_not_awaited()

    async def test_expire_stale_sessions_deactivates_and_promotes(self):
        primary = SimpleNamespace(user_id=1, is_primary=True, expires_at=datetime.utcnow() - timedelta(days=10))
        secondary = SimpleNamespace(user_id=2, is_primary=False, expires_at=datetime.utcnow() - timedelta(days=10))
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_ExecuteResult([primary, secondary]))

        with patch.object(session_expiry, 'AsyncSessionLocal', return_value=_AsyncSessionContext(session)), patch(
            'core.session_expiry.deactivate_session', AsyncMock()
        ) as deactivate_session, patch(
            'core.session_expiry.promote_next_primary', AsyncMock()
        ) as promote_next_primary:
            result = await session_expiry.expire_stale_sessions()

        self.assertEqual(result, 2)
        self.assertEqual(deactivate_session.await_count, 2)
        promote_next_primary.assert_awaited_once_with(session, 1)
        session.commit.assert_awaited_once()

    async def test_session_expiry_loop_logs_errors_and_keeps_looping(self):
        async def cancel_sleep(_delay):
            raise asyncio.CancelledError()

        with patch('core.session_expiry.expire_stale_sessions', AsyncMock(side_effect=RuntimeError('boom'))), patch(
            'core.session_expiry.asyncio.sleep', side_effect=cancel_sleep
        ), patch.object(session_expiry, 'logger') as logger:
            with self.assertRaises(asyncio.CancelledError):
                await session_expiry.session_expiry_loop()

        logger.error.assert_called_once()


if __name__ == '__main__':
    unittest.main()