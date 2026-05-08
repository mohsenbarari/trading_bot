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

        manager.disconnect(good)
        self.assertEqual(manager.active_connections, [])


if __name__ == "__main__":
    unittest.main()