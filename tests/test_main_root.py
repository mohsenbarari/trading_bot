import unittest
from unittest.mock import patch

from fastapi.responses import FileResponse
from starlette.middleware.cors import CORSMiddleware

import main


class MainRootTests(unittest.IsolatedAsyncioTestCase):
    async def test_root_serves_index_when_frontend_exists(self):
        with patch("pathlib.Path.exists", return_value=True):
            result = await main.root()

        self.assertIsInstance(result, FileResponse)
        self.assertTrue(str(result.path).endswith("mini_app_dist/index.html"))

    async def test_root_returns_api_message_when_frontend_missing(self):
        with patch("pathlib.Path.exists", return_value=False):
            result = await main.root()

        self.assertEqual(result, {"message": "Trading Bot API is running 🚀"})

    async def test_app_hides_public_docs_and_uses_explicit_cors_allowlist(self):
        self.assertIsNone(main.app.openapi_url)
        self.assertIsNone(main.app.docs_url)
        self.assertIsNone(main.app.redoc_url)
        self.assertNotIn("*", main.origins)

        cors_entry = next(
            middleware
            for middleware in main.app.user_middleware
            if middleware.cls is CORSMiddleware
        )
        self.assertEqual(cors_entry.kwargs.get("allow_origins"), main.origins)


if __name__ == "__main__":
    unittest.main()