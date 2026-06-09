import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

import main


def make_request(host: str | None, key: str | None = None):
    headers = {}
    if key is not None:
        headers[main.OBSERVABILITY_API_KEY_HEADER] = key
    return SimpleNamespace(client=SimpleNamespace(host=host) if host is not None else None, headers=headers)


class MainMetricsGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_metrics_allows_loopback_without_key(self):
        with patch.object(main.settings, "observability_api_key", None):
            response = await main.get_metrics(make_request("127.0.0.1"))

        self.assertTrue(response.media_type.startswith("text/plain"))
        self.assertIn("trading_bot_process_uptime_seconds", response.body.decode())

    async def test_metrics_rejects_remote_without_observability_key(self):
        with patch.object(main.settings, "observability_api_key", "metrics-key"):
            with self.assertRaises(HTTPException) as exc:
                await main.get_metrics(make_request("203.0.113.10"))

        self.assertEqual(exc.exception.status_code, 404)

    async def test_metrics_allows_remote_with_observability_key(self):
        with patch.object(main.settings, "observability_api_key", "metrics-key"):
            response = await main.get_metrics(make_request("203.0.113.10", "metrics-key"))

        self.assertTrue(response.media_type.startswith("text/plain"))
        self.assertIn("trading_bot_process_uptime_seconds", response.body.decode())

    async def test_metrics_does_not_accept_dev_api_key_header(self):
        request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"), headers={"X-DEV-API-KEY": "dev-key"})

        with patch.object(main.settings, "observability_api_key", "metrics-key"):
            with self.assertRaises(HTTPException) as exc:
                await main.get_metrics(request)

        self.assertEqual(exc.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
