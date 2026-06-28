import unittest
from unittest.mock import patch

from jose import JWTError

from api.routers.commodities import get_request_source


class CommoditiesRouterRequestSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_request_source_prefers_dev_api_key(self):
        with patch("api.routers.commodities.settings.dev_api_key", "dev-key"):
            result = await get_request_source(api_key="dev-key", token=None)
            self.assertEqual(result, "bot")

    async def test_get_request_source_ignores_invalid_dev_api_key(self):
        with patch("api.routers.commodities.settings.dev_api_key", "dev-key"):
            result = await get_request_source(api_key="wrong-key", token=None)
            self.assertEqual(result, "unknown")

    async def test_get_request_source_reads_source_from_token_or_defaults(self):
        with patch("api.routers.commodities.jwt.decode", return_value={"source": "miniapp-web"}):
            self.assertEqual(await get_request_source(api_key=None, token="jwt-token"), "miniapp-web")

        with patch("api.routers.commodities.jwt.decode", return_value={}):
            self.assertEqual(await get_request_source(api_key=None, token="jwt-token"), "miniapp")

    async def test_get_request_source_returns_unknown_for_invalid_or_missing_auth(self):
        with patch("api.routers.commodities.jwt.decode", side_effect=JWTError("bad token")):
            self.assertEqual(await get_request_source(api_key=None, token="jwt-token"), "unknown")

        self.assertEqual(await get_request_source(api_key=None, token=None), "unknown")


if __name__ == "__main__":
    unittest.main()
