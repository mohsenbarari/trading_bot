from contextlib import contextmanager
import json
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, call, patch

from fastapi import WebSocketDisconnect

from api.routers.realtime import manager, websocket_endpoint


class FakeWebSocket:
    def __init__(self, incoming_messages=None):
        self.incoming_messages = list(incoming_messages or [])
        self.accepted = False
        self.close_calls = []
        self.sent_texts = []
        self.sent_jsons = []

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        if not self.incoming_messages:
            raise WebSocketDisconnect(code=1000)
        item = self.incoming_messages.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, type) and issubclass(item, Exception):
            raise item()
        return item

    async def send_text(self, data: str):
        self.sent_texts.append(data)

    async def send_json(self, data: dict):
        self.sent_jsons.append(data)

    async def close(self, code=1000, reason=""):
        self.close_calls.append((code, reason))


class InertAwaitable:
    def __await__(self):
        if False:
            yield None
        return None

    def close(self):
        pass


class FakeTask:
    def __init__(self, target_awaitable):
        self.target_awaitable = target_awaitable
        self.cancel_calls = []
        self.cancel = MagicMock(side_effect=self._record_cancel)

    def _record_cancel(self, *args, **kwargs):
        self.cancel_calls.append((args, kwargs))
        return True

    def cancelled(self) -> bool:
        return len(self.cancel_calls) > 0


class TestP100bCharacterizationRealtimeWebSocketResults(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._reset_connection_manager()
        self.addCleanup(self._reset_connection_manager)

    def tearDown(self):
        self._reset_connection_manager()

    def _reset_connection_manager(self):
        manager.active_connections.clear()
        manager._seen_event_ids.clear()

    @contextmanager
    def _endpoint_harness(
        self,
        *,
        user_id: int,
        session_id: str,
        connection_uuid: uuid.UUID,
        denial_side_effect=None,
        is_market_route_return: bool = True,
    ):
        inert_awaitable = InertAwaitable()
        fake_task = FakeTask(inert_awaitable)
        mock_listen = MagicMock(return_value=inert_awaitable)
        mock_create = MagicMock(return_value=fake_task)

        real_connect = manager.connect
        real_disconnect = manager.disconnect

        denial_mock = AsyncMock()
        if denial_side_effect is not None:
            denial_mock.side_effect = denial_side_effect
        else:
            denial_mock.return_value = None

        with patch("api.routers.realtime.verify_ws_token", return_value=(user_id, session_id)), \
             patch("api.routers.realtime._websocket_access_denial", new=denial_mock) as mock_access_denial, \
             patch("api.routers.realtime.uuid.uuid4", return_value=connection_uuid), \
             patch("api.routers.realtime.listen_redis_events", new=mock_listen) as mock_listen_redis_events, \
             patch("api.routers.realtime.asyncio.create_task", new=mock_create) as mock_create_task, \
             patch("api.routers.realtime.is_market_route", return_value=is_market_route_return) as mock_is_market_route, \
             patch("api.routers.realtime.refresh_market_page_presence", new=AsyncMock()) as mock_refresh_presence, \
             patch("api.routers.realtime.set_market_page_presence", new=AsyncMock()) as mock_set_presence, \
             patch("api.routers.realtime.clear_market_page_presence", new=AsyncMock()) as mock_clear_presence, \
             patch("api.routers.realtime.set_active_websocket_connections"), \
             patch.object(manager, "connect", wraps=real_connect) as mock_connect, \
             patch.object(manager, "disconnect", wraps=real_disconnect) as mock_disconnect:

            yield SimpleNamespace(
                inert_awaitable=inert_awaitable,
                fake_task=fake_task,
                mock_listen_redis_events=mock_listen_redis_events,
                mock_create_task=mock_create_task,
                mock_access_denial=mock_access_denial,
                mock_refresh_presence=mock_refresh_presence,
                mock_set_presence=mock_set_presence,
                mock_clear_presence=mock_clear_presence,
                mock_connect=mock_connect,
                mock_disconnect=mock_disconnect,
                mock_is_market_route=mock_is_market_route,
            )

    async def test_authenticated_connection_receives_ping_and_disconnects(self):
        websocket = FakeWebSocket(incoming_messages=["ping", WebSocketDisconnect(code=1000)])
        deterministic_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        user_id = 42
        session_id = "session-test-42"

        with self._endpoint_harness(
            user_id=user_id,
            session_id=session_id,
            connection_uuid=deterministic_uuid,
        ) as harness:
            await websocket_endpoint(websocket, token="valid-token")

            self.assertTrue(websocket.accepted)
            harness.mock_connect.assert_awaited_once_with(websocket)
            self.assertEqual(websocket.sent_texts, ["pong"])
            self.assertEqual(websocket.sent_jsons, [])
            self.assertEqual(websocket.close_calls, [])

            harness.mock_listen_redis_events.assert_called_once_with(websocket, user_id, session_id)
            harness.mock_create_task.assert_called_once_with(harness.inert_awaitable)

            self.assertEqual(harness.mock_access_denial.await_count, 2)
            harness.mock_access_denial.assert_has_awaits([
                call(user_id, session_id),
                call(user_id, session_id),
            ])

            harness.mock_refresh_presence.assert_awaited_once_with(
                user_id,
                deterministic_uuid.hex,
                active=False,
            )
            harness.mock_set_presence.assert_not_called()

            harness.mock_disconnect.assert_called_once_with(websocket)
            self.assertNotIn(websocket, manager.active_connections)
            self.assertEqual(manager.active_connections, [])

            harness.mock_clear_presence.assert_awaited_once_with(user_id, deterministic_uuid.hex)

            harness.fake_task.cancel.assert_called_once_with()
            self.assertEqual(len(harness.fake_task.cancel_calls), 1)
            self.assertTrue(harness.fake_task.cancelled())

    async def test_presence_update_market_route_updates_presence_and_cleans_up(self):
        deterministic_uuid = uuid.UUID("22222222-2222-2222-2222-222222222222")
        user_id = 55
        session_id = "session-market-55"
        market_path = "/market/live"

        presence_message = json.dumps({
            "type": "presence:update",
            "data": {
                "path": market_path,
                "visible": True,
            },
        })
        websocket = FakeWebSocket(incoming_messages=[presence_message, WebSocketDisconnect(code=1000)])

        with self._endpoint_harness(
            user_id=user_id,
            session_id=session_id,
            connection_uuid=deterministic_uuid,
            is_market_route_return=True,
        ) as harness:
            await websocket_endpoint(websocket, token="valid-token")

            self.assertTrue(websocket.accepted)
            harness.mock_connect.assert_awaited_once_with(websocket)

            harness.mock_set_presence.assert_awaited_once_with(
                user_id,
                deterministic_uuid.hex,
                path=market_path,
                visible=True,
            )
            harness.mock_is_market_route.assert_called_once_with(market_path)

            self.assertEqual(websocket.sent_texts, [])
            self.assertEqual(websocket.sent_jsons, [])
            harness.mock_refresh_presence.assert_not_called()
            self.assertEqual(websocket.close_calls, [])

            self.assertEqual(harness.mock_access_denial.await_count, 2)
            harness.mock_listen_redis_events.assert_called_once_with(websocket, user_id, session_id)
            harness.mock_create_task.assert_called_once_with(harness.inert_awaitable)

            harness.mock_disconnect.assert_called_once_with(websocket)
            self.assertNotIn(websocket, manager.active_connections)
            self.assertEqual(manager.active_connections, [])
            harness.mock_clear_presence.assert_awaited_once_with(user_id, deterministic_uuid.hex)

            harness.fake_task.cancel.assert_called_once_with()
            self.assertEqual(len(harness.fake_task.cancel_calls), 1)
            self.assertTrue(harness.fake_task.cancelled())

    async def test_access_denied_after_successful_connect_and_message_receipt(self):
        deterministic_uuid = uuid.UUID("33333333-3333-3333-3333-333333333333")
        user_id = 77
        session_id = "session-denied-77"
        websocket = FakeWebSocket(incoming_messages=["ping", "unreached-subsequent-message"])

        with self._endpoint_harness(
            user_id=user_id,
            session_id=session_id,
            connection_uuid=deterministic_uuid,
            denial_side_effect=[None, (4003, "Session has been revoked")],
        ) as harness:
            await websocket_endpoint(websocket, token="valid-token")

            self.assertTrue(websocket.accepted)
            harness.mock_connect.assert_awaited_once_with(websocket)

            self.assertEqual(websocket.close_calls, [(4003, "Session has been revoked")])
            self.assertEqual(websocket.sent_texts, [])
            self.assertEqual(websocket.sent_jsons, [])
            harness.mock_refresh_presence.assert_not_called()
            harness.mock_set_presence.assert_not_called()
            self.assertEqual(websocket.incoming_messages, ["unreached-subsequent-message"])

            self.assertEqual(harness.mock_access_denial.await_count, 2)
            harness.mock_access_denial.assert_has_awaits([
                call(user_id, session_id),
                call(user_id, session_id),
            ])

            harness.mock_listen_redis_events.assert_called_once_with(websocket, user_id, session_id)
            harness.mock_create_task.assert_called_once_with(harness.inert_awaitable)

            harness.mock_disconnect.assert_called_once_with(websocket)
            self.assertNotIn(websocket, manager.active_connections)
            self.assertEqual(manager.active_connections, [])
            harness.mock_clear_presence.assert_awaited_once_with(user_id, deterministic_uuid.hex)

            harness.fake_task.cancel.assert_called_once_with()
            self.assertEqual(len(harness.fake_task.cancel_calls), 1)
            self.assertTrue(harness.fake_task.cancelled())


if __name__ == "__main__":
    unittest.main()
