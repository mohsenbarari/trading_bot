import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.application_writer_term import ApplicationWriterTermError
from api.routers import realtime
from api.routers.realtime import websocket_endpoint


class FakeWebSocket:
    def __init__(self):
        self.close_calls = []

    async def close(self, code, reason):
        self.close_calls.append((code, reason))


class FakeSession:
    def __init__(self, user=None, active_session=None):
        self.user = user
        self.active_session = active_session

    async def get(self, model, key):
        name = getattr(model, "__name__", str(model))
        if name == "User":
            return self.user
        return self.active_session


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class RealtimeRouterWebSocketGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_writer_term_disabled_preserves_missing_token_rejection(self):
        websocket = FakeWebSocket()

        with patch("api.routers.realtime.settings.application_writer_term_enforced", False), patch(
            "api.routers.realtime.require_application_writer_term"
        ) as require_term:
            await websocket_endpoint(websocket, token=None)

        self.assertEqual(websocket.close_calls, [(4001, "Missing authentication token")])
        require_term.assert_not_called()

    async def test_websocket_rejects_invalid_or_near_expiry_term_before_accept_or_presence(self):
        for reason in ("writer term is expired", "writer term expires within the required safety margin"):
            with self.subTest(reason=reason):
                websocket = FakeWebSocket()
                with patch(
                    "api.routers.realtime.settings.application_writer_term_enforced", True
                ), patch(
                    "api.routers.realtime.require_application_writer_term",
                    side_effect=ApplicationWriterTermError(reason),
                ) as require_term, patch(
                    "api.routers.realtime.manager.connect", new=AsyncMock()
                ) as connect_mock, patch(
                    "api.routers.realtime.set_market_page_presence", new=AsyncMock()
                ) as set_presence_mock, patch(
                    "api.routers.realtime.refresh_market_page_presence", new=AsyncMock()
                ) as refresh_presence_mock, patch(
                    "api.routers.realtime.clear_market_page_presence", new=AsyncMock()
                ) as clear_presence_mock, patch(
                    "api.routers.realtime.verify_ws_token"
                ) as verify_token:
                    await websocket_endpoint(websocket, token="jwt")

                self.assertEqual(
                    websocket.close_calls,
                    [(1013, "Service temporarily unavailable")],
                )
                require_term.assert_called_once_with()
                connect_mock.assert_not_awaited()
                set_presence_mock.assert_not_awaited()
                refresh_presence_mock.assert_not_awaited()
                clear_presence_mock.assert_not_awaited()
                verify_token.assert_not_called()

    async def test_websocket_access_recheck_fails_before_session_or_presence_io(self):
        with patch(
            "api.routers.realtime.settings.application_writer_term_enforced", True
        ), patch(
            "api.routers.realtime.require_application_writer_term",
            side_effect=ApplicationWriterTermError("writer term is expired"),
        ), patch(
            "api.routers.realtime.is_session_blacklisted", new=AsyncMock()
        ) as blacklist_check, patch(
            "api.routers.realtime.AsyncSessionLocal"
        ) as session_factory:
            denial = await realtime._websocket_access_denial(5, "session-id")

        self.assertEqual(denial, (1013, "Service temporarily unavailable"))
        blacklist_check.assert_not_awaited()
        session_factory.assert_not_called()

    async def test_websocket_auth_epoch_rejects_legacy_sessionless_jwt_before_user_io(self):
        session = FakeSession(user=SimpleNamespace(id=5, is_deleted=False))
        with patch(
            "api.routers.realtime.is_session_blacklisted", new=AsyncMock(return_value=False)
        ), patch(
            "api.routers.realtime.AsyncSessionLocal",
            return_value=FakeSessionContext(session),
        ), patch(
            "api.routers.realtime.enforce_access_token_auth_epoch",
            new=AsyncMock(side_effect=realtime.PromotionAccessTokenEpochError("legacy")),
        ):
            denial = await realtime._websocket_access_denial(
                5,
                None,
                token_payload={"sub": "5", "type": "access"},
            )

        self.assertEqual(denial, (4003, "Session has been revoked"))

    async def test_redis_listener_refuses_invalid_term_before_redis_connection(self):
        websocket = FakeWebSocket()
        with patch(
            "api.routers.realtime.settings.application_writer_term_enforced", True
        ), patch(
            "api.routers.realtime.require_application_writer_term",
            side_effect=ApplicationWriterTermError("writer term is expired"),
        ), patch("api.routers.realtime.redis.Redis") as redis_constructor:
            await realtime.listen_redis_events(websocket, user_id=5, session_id="session-id")

        self.assertEqual(websocket.close_calls, [(1013, "Service temporarily unavailable")])
        redis_constructor.assert_not_called()

    async def test_sse_generator_ends_before_redis_connection_when_term_is_invalid(self):
        with patch(
            "api.routers.realtime.settings.application_writer_term_enforced", True
        ), patch(
            "api.routers.realtime.require_application_writer_term",
            side_effect=ApplicationWriterTermError("writer term is expired"),
        ), patch("api.routers.realtime.redis.Redis") as redis_constructor:
            stream = realtime.event_generator(5, session_id="session-id")
            with self.assertRaises(StopAsyncIteration):
                await anext(stream)

        redis_constructor.assert_not_called()

    async def test_websocket_endpoint_rejects_missing_or_invalid_token(self):
        websocket = FakeWebSocket()
        await websocket_endpoint(websocket, token=None)
        self.assertEqual(websocket.close_calls, [(4001, "Missing authentication token")])

        websocket = FakeWebSocket()
        with patch("api.routers.realtime.verify_ws_token", return_value=None):
            await websocket_endpoint(websocket, token="bad")
        self.assertEqual(websocket.close_calls, [(4003, "Invalid or expired token")])

    async def test_websocket_endpoint_rejects_blacklisted_deleted_invalid_and_revoked_sessions(self):
        websocket = FakeWebSocket()
        with patch("api.routers.realtime.verify_ws_token", return_value=(5, "session-id")), patch(
            "api.routers.realtime.is_session_blacklisted", new=AsyncMock(return_value=True)
        ):
            await websocket_endpoint(websocket, token="jwt")
        self.assertEqual(websocket.close_calls, [(4003, "Session has been revoked")])

        websocket = FakeWebSocket()
        with patch("api.routers.realtime.verify_ws_token", return_value=(5, None)), patch(
            "api.routers.realtime.is_session_blacklisted", new=AsyncMock(return_value=False)
        ), patch("api.routers.realtime.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(user=SimpleNamespace(id=5, is_deleted=True)))):
            await websocket_endpoint(websocket, token="jwt")
        self.assertEqual(websocket.close_calls, [(4003, "User is inactive")])

        websocket = FakeWebSocket()
        with patch("api.routers.realtime.verify_ws_token", return_value=(5, "not-a-uuid")), patch(
            "api.routers.realtime.is_session_blacklisted", new=AsyncMock(return_value=False)
        ), patch("api.routers.realtime.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(user=SimpleNamespace(id=5, is_deleted=False)))):
            await websocket_endpoint(websocket, token="jwt")
        self.assertEqual(websocket.close_calls, [(4003, "Invalid session")])

        websocket = FakeWebSocket()
        with patch("api.routers.realtime.verify_ws_token", return_value=(5, str(uuid.uuid4()))), patch(
            "api.routers.realtime.is_session_blacklisted", new=AsyncMock(return_value=False)
        ), patch(
            "api.routers.realtime.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(user=SimpleNamespace(id=5, is_deleted=False), active_session=SimpleNamespace(is_active=False, user_id=5))),
        ):
            await websocket_endpoint(websocket, token="jwt")
        self.assertEqual(websocket.close_calls, [(4003, "Session has been revoked")])

    async def test_websocket_endpoint_rejects_globally_web_locked_user(self):
        websocket = FakeWebSocket()
        user = SimpleNamespace(id=5, is_deleted=False)
        with patch("api.routers.realtime.verify_ws_token", return_value=(5, None)), patch(
            "api.routers.realtime.is_session_blacklisted", new=AsyncMock(return_value=False)
        ), patch(
            "api.routers.realtime.is_user_global_web_locked", return_value=True
        ), patch(
            "api.routers.realtime.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(user=user)),
        ):
            await websocket_endpoint(websocket, token="jwt")

        self.assertEqual(websocket.close_calls, [(4003, "User is inactive")])


if __name__ == "__main__":
    unittest.main()
