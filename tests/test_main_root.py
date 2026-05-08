import unittest
from unittest.mock import patch

from fastapi.responses import FileResponse

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


if __name__ == "__main__":
    unittest.main()