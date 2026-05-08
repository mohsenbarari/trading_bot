import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.blocks import search_users


class BlocksRouterSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_users_delegates_to_service_with_explicit_query_and_limit(self):
        current_user = SimpleNamespace(id=5)
        rows = [SimpleNamespace(id=9, account_name="ali", is_blocked=False)]

        with patch("api.routers.blocks.search_users_for_block", new=AsyncMock(return_value=rows)) as service_mock:
            result = await search_users(q="09", limit=12, db=SimpleNamespace(), current_user=current_user)

        service_mock.assert_awaited_once_with(unittest.mock.ANY, "09", 5, 12)
        self.assertEqual(result, rows)


if __name__ == "__main__":
    unittest.main()