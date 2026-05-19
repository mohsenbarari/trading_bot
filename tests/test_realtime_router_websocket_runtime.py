import asyncio
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import WebSocketDisconnect

from api.routers.realtime import websocket_endpoint


class FakeTask:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeWebSocket:
    def __init__(self):
        self.sent_json = []
        self.sent_text = []
        self.close_calls = []

    async def receive_text(self):
        return "unused"

    async def close(self, code, reason):
        self.close_calls.append((code, reason))

    async def send_json(self, payload):
        self.sent_json.append(payload)

    async def send_text(self, payload):
        self.sent_text.append(payload)


class FailingHeartbeatWebSocket(FakeWebSocket):
    async def send_json(self, payload):
        raise RuntimeError("heartbeat failed")


class ExplodingReceiveWebSocket(FakeWebSocket):
    async def receive_text(self):
        raise RuntimeError("socket exploded")


class FakeSession:
    def __init__(self, user, active_session):
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


class RealtimeRouterWebSocketRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_endpoint_handles_ping_disconnect_and_cleanup(self):
        websocket = FakeWebSocket()
        task = FakeTask()
        session_id = str(uuid.uuid4())

        async def fake_wait_for(awaitable, timeout):
            if hasattr(awaitable, "close"):
                awaitable.close()
            fake_wait_for.calls += 1
            if fake_wait_for.calls == 1:
                return "ping"
            raise WebSocketDisconnect()
        fake_wait_for.calls = 0

        def fake_create_task(coro):
            if hasattr(coro, "close"):
                coro.close()
            return task

        with patch("api.routers.realtime.verify_ws_token", return_value=(5, session_id)), patch(
            "api.routers.realtime.is_session_blacklisted", new=AsyncMock(return_value=False)
        ), patch(
            "api.routers.realtime.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(user=SimpleNamespace(id=5, is_deleted=False), active_session=SimpleNamespace(is_active=True, user_id=5))),
        ), patch("api.routers.realtime.manager.connect", new=AsyncMock()) as connect_mock, patch(
            "api.routers.realtime.manager.disconnect"
        ) as disconnect_mock, patch("api.routers.realtime.asyncio.create_task", side_effect=fake_create_task), patch(
            "api.routers.realtime.asyncio.wait_for", side_effect=fake_wait_for
        ):
            await websocket_endpoint(websocket, token="jwt")

        connect_mock.assert_awaited_once_with(websocket)
        disconnect_mock.assert_called_once_with(websocket)
        self.assertEqual(websocket.sent_text, ["pong"])
        self.assertTrue(task.cancelled)

    async def test_websocket_endpoint_sends_heartbeat_on_timeout(self):
        websocket = FakeWebSocket()
        task = FakeTask()
        session_id = str(uuid.uuid4())

        async def fake_wait_for(awaitable, timeout):
            if hasattr(awaitable, "close"):
                awaitable.close()
            fake_wait_for.calls += 1
            if fake_wait_for.calls == 1:
                raise asyncio.TimeoutError()
            raise WebSocketDisconnect()
        fake_wait_for.calls = 0

        def fake_create_task(coro):
            if hasattr(coro, "close"):
                coro.close()
            return task

        with patch("api.routers.realtime.verify_ws_token", return_value=(5, session_id)), patch(
            "api.routers.realtime.is_session_blacklisted", new=AsyncMock(return_value=False)
        ), patch(
            "api.routers.realtime.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(user=SimpleNamespace(id=5, is_deleted=False), active_session=SimpleNamespace(is_active=True, user_id=5))),
        ), patch("api.routers.realtime.manager.connect", new=AsyncMock()), patch(
            "api.routers.realtime.manager.disconnect"
        ), patch("api.routers.realtime.asyncio.create_task", side_effect=fake_create_task), patch(
            "api.routers.realtime.asyncio.wait_for", side_effect=fake_wait_for
        ):
            await websocket_endpoint(websocket, token="jwt")

        self.assertEqual(websocket.sent_json, [{"type": "heartbeat"}])
        self.assertTrue(task.cancelled)

    async def test_websocket_endpoint_breaks_when_heartbeat_send_fails(self):
        websocket = FailingHeartbeatWebSocket()
        task = FakeTask()
        session_id = str(uuid.uuid4())

        async def fake_wait_for(awaitable, timeout):
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise asyncio.TimeoutError()

        def fake_create_task(coro):
            if hasattr(coro, "close"):
                coro.close()
            return task

        with patch("api.routers.realtime.verify_ws_token", return_value=(5, session_id)), patch(
            "api.routers.realtime.is_session_blacklisted", new=AsyncMock(return_value=False)
        ), patch(
            "api.routers.realtime.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(user=SimpleNamespace(id=5, is_deleted=False), active_session=SimpleNamespace(is_active=True, user_id=5))),
        ), patch("api.routers.realtime.manager.connect", new=AsyncMock()), patch(
            "api.routers.realtime.manager.disconnect"
        ) as disconnect_mock, patch("api.routers.realtime.asyncio.create_task", side_effect=fake_create_task), patch(
            "api.routers.realtime.asyncio.wait_for", side_effect=fake_wait_for
        ):
            await websocket_endpoint(websocket, token="jwt")

        disconnect_mock.assert_called_once_with(websocket)
        self.assertTrue(task.cancelled)

    async def test_websocket_endpoint_logs_outer_runtime_errors(self):
        websocket = ExplodingReceiveWebSocket()
        task = FakeTask()
        session_id = str(uuid.uuid4())

        async def fake_wait_for(awaitable, timeout):
            return await awaitable

        def fake_create_task(coro):
            if hasattr(coro, "close"):
                coro.close()
            return task

        with patch("api.routers.realtime.verify_ws_token", return_value=(5, session_id)), patch(
            "api.routers.realtime.is_session_blacklisted", new=AsyncMock(return_value=False)
        ), patch(
            "api.routers.realtime.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(user=SimpleNamespace(id=5, is_deleted=False), active_session=SimpleNamespace(is_active=True, user_id=5))),
        ), patch("api.routers.realtime.manager.connect", new=AsyncMock()), patch(
            "api.routers.realtime.manager.disconnect"
        ), patch("api.routers.realtime.asyncio.create_task", side_effect=fake_create_task), patch(
            "api.routers.realtime.asyncio.wait_for", side_effect=fake_wait_for
        ), patch("api.routers.realtime.logging.warning") as warning_mock:
            await websocket_endpoint(websocket, token="jwt")

        warning_mock.assert_called_once()
        self.assertTrue(task.cancelled)


if __name__ == "__main__":
    unittest.main()