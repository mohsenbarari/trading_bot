import unittest
from unittest.mock import patch

import main


class MainPublicConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_public_config_returns_non_sensitive_settings(self):
        with patch.object(main.settings, "bot_username", "bot_user"), patch.object(main.settings, "frontend_url", "https://front.example"):
            result = await main.get_public_config()

        self.assertEqual(result, {"bot_username": "bot_user", "frontend_url": "https://front.example"})


if __name__ == "__main__":
    unittest.main()