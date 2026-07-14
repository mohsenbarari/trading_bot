import unittest

from api.routers.realtime import ConnectionManager


class GoodWebSocket:
    def __init__(self):
        self.accepted = 0
        self.messages = []

    async def accept(self):
        self.accepted += 1

    async def send_json(self, message):
        self.messages.append(message)


class BadWebSocket(GoodWebSocket):
    async def send_json(self, message):
        raise RuntimeError("closed")


class RealtimeRouterManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_manager_connect_disconnect_and_broadcast(self):
        manager = ConnectionManager()
        good = GoodWebSocket()
        bad = BadWebSocket()

        await manager.connect(good)
        await manager.connect(bad)
        self.assertEqual(good.accepted, 1)
        self.assertEqual(len(manager.active_connections), 2)

        await manager.broadcast({"type": "offer"})
        self.assertEqual(good.messages, [{"type": "offer"}])
        self.assertEqual(manager.active_connections, [good])

        event = {"type": "offer:updated", "data": {"id": 1}, "event_id": "event-1"}
        await manager.broadcast(event)
        await manager.broadcast(event)
        self.assertEqual(good.messages.count(event), 1)
        next_event = {"type": "offer:updated", "data": {"id": 1}, "event_id": "event-2"}
        await manager.broadcast(next_event)
        self.assertEqual(good.messages.count(next_event), 1)

        manager.disconnect(good)
        self.assertEqual(manager.active_connections, [])
        self.assertEqual(manager._seen_event_ids, {})

    async def test_independent_worker_managers_deduplicate_per_connection(self):
        worker_a = ConnectionManager()
        worker_b = ConnectionManager()
        socket_a = GoodWebSocket()
        socket_b = GoodWebSocket()
        await worker_a.connect(socket_a)
        await worker_b.connect(socket_b)

        event = {"type": "offer:updated", "data": {"id": 9}, "event_id": "shared-event"}
        for worker in (worker_a, worker_b):
            await worker.broadcast(event)
            await worker.broadcast(event)

        self.assertEqual(socket_a.messages, [event])
        self.assertEqual(socket_b.messages, [event])

        worker_a.disconnect(socket_a)
        worker_b.disconnect(socket_b)


if __name__ == "__main__":
    unittest.main()
