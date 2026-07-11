import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from core.services.invitation_public_access_service import (
    PUBLIC_INVITATION_RATE_WINDOW_SECONDS,
    _rate_limit_key,
    enforce_public_invitation_access,
)


def _request(path: str, route_path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "client": ("203.0.113.10", 12345),
            "server": ("staging.gold-trade.ir", 443),
            "route": SimpleNamespace(path=route_path),
        }
    )


class _Redis:
    def __init__(self, *, count=1, fail=False):
        self.count = count
        self.fail = fail
        self.keys = []
        self.expirations = []

    async def eval(self, script, number_of_keys, key, seconds):
        if self.fail:
            raise RuntimeError("redis unavailable")
        self.script = script
        self.number_of_keys = number_of_keys
        self.keys.append(key)
        if self.count == 1:
            self.expirations.append((key, seconds))
        return self.count


class InvitationPublicAccessTests(unittest.IsolatedAsyncioTestCase):
    def test_rate_limit_key_uses_route_template_not_raw_secret(self):
        first = _request(
            "/api/invitations/validate/INV-first-secret",
            "/api/invitations/validate/{token}",
        )
        second = _request(
            "/api/invitations/validate/INV-second-secret",
            "/api/invitations/validate/{token}",
        )
        lookup = _request(
            "/api/invitations/lookup/short-secret",
            "/api/invitations/lookup/{short_code}",
        )

        with patch(
            "core.services.invitation_public_access_service.client_ip_from_request",
            return_value="203.0.113.10",
        ):
            validate_key = _rate_limit_key(first)
            self.assertEqual(validate_key, _rate_limit_key(second))
            self.assertNotEqual(validate_key, _rate_limit_key(lookup))

        self.assertNotIn("INV-first-secret", validate_key)
        self.assertNotIn("203.0.113.10", validate_key)

    async def test_success_sets_no_store_headers_and_one_window(self):
        redis = _Redis(count=1)
        response = Response()
        request = _request(
            "/api/invitations/validate/INV-secret",
            "/api/invitations/validate/{token}",
        )

        with patch(
            "core.services.invitation_public_access_service.get_redis_client",
            return_value=redis,
        ), patch(
            "core.services.invitation_public_access_service.settings.invitation_public_rate_limit_per_minute",
            30,
        ):
            await enforce_public_invitation_access(request, response)

        self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(redis.number_of_keys, 1)
        self.assertIn("EXPIRE", redis.script)
        self.assertEqual(redis.expirations, [(redis.keys[0], PUBLIC_INVITATION_RATE_WINDOW_SECONDS)])

    async def test_rate_limit_error_preserves_security_and_retry_headers(self):
        response = Response()
        request = _request(
            "/api/invitations/lookup/secret",
            "/api/invitations/lookup/{short_code}",
        )
        with patch(
            "core.services.invitation_public_access_service.get_redis_client",
            return_value=_Redis(count=31),
        ), patch(
            "core.services.invitation_public_access_service.settings.invitation_public_rate_limit_per_minute",
            30,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await enforce_public_invitation_access(request, response)

        self.assertEqual(exc_info.exception.status_code, 429)
        self.assertEqual(exc_info.exception.headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(
            exc_info.exception.headers["Retry-After"],
            str(PUBLIC_INVITATION_RATE_WINDOW_SECONDS),
        )

    async def test_missing_or_failed_redis_fails_closed_with_no_store(self):
        request = _request(
            "/api/invitations/validate/INV-secret",
            "/api/invitations/validate/{token}",
        )
        providers = [
            RuntimeError("not initialized"),
            _Redis(fail=True),
        ]
        for provider in providers:
            response = Response()
            side_effect = provider if isinstance(provider, Exception) else None
            return_value = None if side_effect else provider
            with self.subTest(provider=type(provider).__name__), patch(
                "core.services.invitation_public_access_service.get_redis_client",
                side_effect=side_effect,
                return_value=return_value,
            ), patch(
                "core.services.invitation_public_access_service.settings.invitation_public_rate_limit_per_minute",
                30,
            ):
                with self.assertRaises(HTTPException) as exc_info:
                    await enforce_public_invitation_access(request, response)
                self.assertEqual(exc_info.exception.status_code, 503)
                self.assertEqual(exc_info.exception.headers["Cache-Control"], "no-store, max-age=0")

    async def test_zero_limit_disables_public_lookup_without_touching_redis(self):
        request = _request(
            "/api/invitations/validate/INV-secret",
            "/api/invitations/validate/{token}",
        )
        with patch(
            "core.services.invitation_public_access_service.settings.invitation_public_rate_limit_per_minute",
            0,
        ), patch(
            "core.services.invitation_public_access_service.get_redis_client"
        ) as redis_factory:
            with self.assertRaises(HTTPException) as exc_info:
                await enforce_public_invitation_access(request, Response())

        self.assertEqual(exc_info.exception.status_code, 503)
        redis_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
