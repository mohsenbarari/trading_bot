import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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


if __name__ == "__main__":
    unittest.main()