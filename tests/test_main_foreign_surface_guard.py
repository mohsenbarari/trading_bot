import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.responses import JSONResponse
from httpx import ASGITransport

import main


def _request(path: str, *, client_host: str = "198.51.100.10"):
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        client=SimpleNamespace(host=client_host),
    )


async def _call_app(path: str, *, client_host: str = "198.51.100.10") -> httpx.Response:
    transport = ASGITransport(app=main.app, client=(client_host, 123))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)


async def _post_app(
    path: str,
    *,
    client_host: str = "198.51.100.10",
    headers: dict[str, str] | None = None,
    content: bytes = b"{}",
) -> httpx.Response:
    transport = ASGITransport(app=main.app, client=(client_host, 123))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(path, headers=headers, content=content)


class MainForeignSurfaceGuardTests(unittest.IsolatedAsyncioTestCase):
    def test_foreign_mode_blocks_frontend_static_and_spa_paths(self):
        with patch.object(main.settings, "server_mode", "foreign"):
            for path in ("/", "/assets/app.js", "/chat/thread/123", "/market/offers"):
                with self.subTest(path=path):
                    self.assertEqual(
                        main._foreign_surface_guard_reason(_request(path)),
                        "foreign_frontend_surface_blocked",
                    )

    def test_foreign_mode_blocks_public_webapp_apis_and_chat(self):
        blocked_paths = {
            "/api/chat": "foreign_chat_surface_blocked",
            "/api/chat/conversations": "foreign_chat_surface_blocked",
            "/api/auth/login": "foreign_webapp_api_blocked",
            "/api/offers": "foreign_webapp_api_blocked",
            "/api/config": "foreign_webapp_api_blocked",
        }
        with patch.object(main.settings, "server_mode", "foreign"):
            for path, expected_reason in blocked_paths.items():
                with self.subTest(path=path):
                    self.assertEqual(
                        main._foreign_surface_guard_reason(_request(path)),
                        expected_reason,
                    )

    def test_foreign_mode_allows_explicit_internal_routes(self):
        with patch.object(main.settings, "server_mode", "foreign"):
            for path in (
                "/api/sync/receive",
                "/api/sync/health",
                "/api/sessions/internal/authority-check",
                "/api/sessions/internal/reset-user-sessions",
                "/api/trades/internal/execute",
                "/api/offers/internal/expire",
                "/api/auth/internal/telegram-otp/deliver",
                "/metrics",
            ):
                with self.subTest(path=path):
                    self.assertIsNone(main._foreign_surface_guard_reason(_request(path)))

    def test_foreign_mode_allows_config_only_for_loopback_healthcheck(self):
        with patch.object(main.settings, "server_mode", "foreign"):
            self.assertIsNone(
                main._foreign_surface_guard_reason(
                    _request("/api/config", client_host="127.0.0.1")
                )
            )
            self.assertEqual(
                main._foreign_surface_guard_reason(
                    _request("/api/config", client_host="198.51.100.10")
                ),
                "foreign_webapp_api_blocked",
            )

    def test_foreign_mode_does_not_allow_similar_unregistered_prefixes(self):
        with patch.object(main.settings, "server_mode", "foreign"):
            for path in (
                "/api/sync-health",
                "/api/sessions/internalized",
                "/api/trades/internalized",
                "/api/offers/internalized",
            ):
                with self.subTest(path=path):
                    self.assertEqual(
                        main._foreign_surface_guard_reason(_request(path)),
                        "foreign_webapp_api_blocked",
                    )

    def test_iran_mode_allows_webapp_static_chat_and_public_apis(self):
        with patch.object(main.settings, "server_mode", "iran"):
            for path in (
                "/",
                "/assets/app.js",
                "/chat/thread/123",
                "/api/chat/conversations",
                "/api/auth/login",
                "/api/offers",
                "/api/config",
            ):
                with self.subTest(path=path):
                    self.assertIsNone(main._foreign_surface_guard_reason(_request(path)))

    async def test_middleware_returns_404_and_skips_downstream_for_blocked_foreign_route(self):
        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        with patch.object(main.settings, "server_mode", "foreign"), patch.object(main.logger, "warning"):
            response = await main.enforce_foreign_surface_guard(
                _request("/api/chat/conversations"),
                call_next,
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.body, b'{"detail":"Not Found"}')
        call_next.assert_not_awaited()

    async def test_middleware_passes_allowed_internal_route_in_foreign_mode(self):
        expected_response = JSONResponse({"status": "ok"})
        call_next = AsyncMock(return_value=expected_response)

        with patch.object(main.settings, "server_mode", "foreign"):
            response = await main.enforce_foreign_surface_guard(
                _request("/api/sync/health"),
                call_next,
            )

        self.assertIs(response, expected_response)
        call_next.assert_awaited_once()

    async def test_app_blocks_foreign_public_config_but_keeps_loopback_healthcheck(self):
        with patch.object(main.settings, "server_mode", "foreign"), patch.object(main.logger, "warning"):
            public_response = await _call_app("/api/config", client_host="198.51.100.10")
            loopback_response = await _call_app("/api/config", client_host="127.0.0.1")

        self.assertEqual(public_response.status_code, 404)
        self.assertEqual(public_response.json(), {"detail": "Not Found"})
        self.assertEqual(loopback_response.status_code, 200)

    async def test_app_serves_public_config_in_iran_mode(self):
        with patch.object(main.settings, "server_mode", "iran"), patch.object(
            main.settings, "bot_username", "bot_user"
        ), patch.object(main.settings, "frontend_url", "https://iran.example"):
            response = await _call_app("/api/config", client_host="198.51.100.10")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"bot_username": "bot_user", "frontend_url": "https://iran.example"},
        )

    async def test_app_routes_signed_telegram_otp_surface_only_to_foreign_endpoint(self):
        headers = {"X-Source-Server": "iran"}
        with patch.object(main.settings, "server_mode", "foreign"), patch(
            "api.routers.auth.verify_internal_signature",
            return_value=True,
        ):
            foreign_response = await _post_app(
                "/api/auth/internal/telegram-otp/deliver",
                headers=headers,
            )
        self.assertEqual(foreign_response.status_code, 422)
        self.assertEqual(foreign_response.json(), {"detail": "Invalid internal OTP delivery command"})

        with patch.object(main.settings, "server_mode", "iran"), patch(
            "api.routers.auth.verify_internal_signature",
            return_value=True,
        ):
            iran_response = await _post_app(
                "/api/auth/internal/telegram-otp/deliver",
                headers=headers,
            )
        self.assertEqual(iran_response.status_code, 403)
        self.assertEqual(iran_response.json(), {"detail": "Telegram OTP delivery is foreign-only"})


if __name__ == "__main__":
    unittest.main()
