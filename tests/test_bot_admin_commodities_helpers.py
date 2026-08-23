import json
import unittest
from types import SimpleNamespace

import httpx

from bot.handlers.admin_commodities import (
    commodities_api_url,
    commodity_aliases_api_url,
    get_auth_headers,
    get_error_detail,
)


class FakeResponse:
    def __init__(self, payload=None, text="ERR", json_error=False):
        self._payload = payload
        self.text = text
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise json.JSONDecodeError("bad", "x", 0)
        return self._payload


class BotAdminCommoditiesHelperTests(unittest.TestCase):
    def test_api_urls_follow_the_runtime_foreign_service_origin(self):
        from bot.handlers import admin_commodities as module

        original = module.settings.foreign_server_url
        try:
            module.settings.foreign_server_url = "http://foreign_app:8000/"
            self.assertEqual(
                commodities_api_url(),
                "http://foreign_app:8000/api/commodities/",
            )
            self.assertEqual(
                commodity_aliases_api_url(),
                "http://foreign_app:8000/api/commodities/aliases/",
            )

            module.settings.foreign_server_url = None
            self.assertEqual(
                commodities_api_url(),
                "http://app:8000/api/commodities/",
            )
        finally:
            module.settings.foreign_server_url = original

    def test_get_auth_headers_uses_dev_key_or_not_set(self):
        from bot.handlers import admin_commodities as module

        original = module.settings.dev_api_key
        try:
            module.settings.dev_api_key = None
            self.assertEqual(get_auth_headers(), {"X-DEV-API-KEY": "NOT_SET"})

            module.settings.dev_api_key = "secret"
            self.assertEqual(get_auth_headers(), {"X-DEV-API-KEY": "secret"})
        finally:
            module.settings.dev_api_key = original

    def test_get_error_detail_handles_string_dict_and_invalid_json(self):
        error = httpx.HTTPStatusError("bad", request=SimpleNamespace(), response=FakeResponse({"detail": "oops"}))
        self.assertEqual(get_error_detail(error), "oops")

        error = httpx.HTTPStatusError("bad", request=SimpleNamespace(), response=FakeResponse({"detail": ["a", "b"]}))
        self.assertEqual(get_error_detail(error), '["a", "b"]')

        error = httpx.HTTPStatusError("bad", request=SimpleNamespace(), response=FakeResponse({"detail": None}, text="fallback"))
        self.assertEqual(get_error_detail(error), "fallback")

        error = httpx.HTTPStatusError("bad", request=SimpleNamespace(), response=FakeResponse(json_error=True, text="plain"))
        self.assertEqual(get_error_detail(error), "plain")


if __name__ == "__main__":
    unittest.main()
