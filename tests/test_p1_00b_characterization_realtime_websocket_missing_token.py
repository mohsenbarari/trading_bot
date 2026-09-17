"""Hermetic characterization test for realtime WebSocket missing token rejection."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from unittest.mock import AsyncMock, patch

from api.routers import realtime
from api.routers.realtime import websocket_endpoint


class StrictWebSocketDouble:
    """Strict double for WebSocket that records close and rejects all traffic."""

    def __init__(self) -> None:
        self.close_calls: list[tuple[int, str]] = []

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append((code, reason))

    async def accept(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("StrictWebSocketDouble: accept must not be called")

    async def receive_text(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("StrictWebSocketDouble: receive_text must not be called")

    async def send_text(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("StrictWebSocketDouble: send_text must not be called")

    async def send_json(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("StrictWebSocketDouble: send_json must not be called")


class RealtimeWebsocketMissingTokenCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_token_closes_4001_before_auth_access_or_listener(self) -> None:
        websocket = StrictWebSocketDouble()
        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_constructs_before = len(fence["live_redis_constructs"])

        with (
            patch("api.routers.realtime.verify_ws_token") as mock_verify_ws_token,
            patch(
                "api.routers.realtime._websocket_access_denial",
                new_callable=AsyncMock,
            ) as mock_access_denial,
            patch("api.routers.realtime.uuid.uuid4") as mock_uuid4,
            patch.object(
                realtime.manager,
                "connect",
                new_callable=AsyncMock,
            ) as mock_manager_connect,
            patch.object(
                realtime.manager,
                "disconnect",
            ) as mock_manager_disconnect,
            patch(
                "api.routers.realtime.listen_redis_events",
                new_callable=AsyncMock,
            ) as mock_listen_redis_events,
            patch("api.routers.realtime.asyncio.create_task") as mock_create_task,
        ):
            await websocket_endpoint(websocket, token=None)

        self.assertEqual(
            websocket.close_calls,
            [(4001, "Missing authentication token")],
        )
        self.assertEqual(len(websocket.close_calls), 1)

        mock_verify_ws_token.assert_not_called()
        mock_access_denial.assert_not_called()
        mock_access_denial.assert_not_awaited()
        mock_uuid4.assert_not_called()
        mock_manager_connect.assert_not_called()
        mock_manager_connect.assert_not_awaited()
        mock_manager_disconnect.assert_not_called()
        mock_listen_redis_events.assert_not_called()
        mock_listen_redis_events.assert_not_awaited()
        mock_create_task.assert_not_called()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_constructs_before)


if __name__ == "__main__":
    unittest.main()
