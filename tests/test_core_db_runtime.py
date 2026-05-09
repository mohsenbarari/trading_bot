from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core import db as db_module


class _AsyncSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _AsyncConnectionContext:
    def __init__(self, connection):
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class CoreDbRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_db_yields_session_and_closes_it(self):
        session = AsyncMock()
        with patch.object(db_module, 'AsyncSessionLocal', return_value=_AsyncSessionContext(session)):
            agen = db_module.get_db()
            yielded = await anext(agen)
            self.assertIs(yielded, session)
            await agen.aclose()

        session.close.assert_awaited_once()

    async def test_init_db_runs_metadata_create_all(self):
        connection = AsyncMock()
        fake_engine = SimpleNamespace(begin=lambda: _AsyncConnectionContext(connection))
        with patch.object(db_module, 'engine', fake_engine):
            await db_module.init_db()

        connection.run_sync.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()