import asyncio
import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from core import sync_worker


class FakeRedis:
    def __init__(self, blpop_results):
        self._blpop_results = list(blpop_results)
        self.rpush_calls = []
        self.blpop_calls = []

    async def blpop(self, queues, timeout=0):
        self.blpop_calls.append((tuple(queues), timeout))
        if not self._blpop_results:
            raise asyncio.CancelledError()
        result = self._blpop_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def rpush(self, queue_name, payload):
        self.rpush_calls.append((queue_name, payload))


class FakeAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class SendSyncItemTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_sync_item_posts_expected_signed_payload(self):
        fake_response = object()
        client = AsyncMock()
        client.post.return_value = fake_response
        item = {"hash": "abc", "table": "offers"}
        timestamp = 1700000000

        with patch("core.sync_worker.time.time", return_value=timestamp):
            response = await sync_worker.send_sync_item(
                client,
                item,
                "https://peer.example",
                "secret-key",
            )

        self.assertIs(response, fake_response)
        client.post.assert_awaited_once()
        _, kwargs = client.post.await_args
        self.assertEqual(kwargs["content"], json.dumps([item], sort_keys=True))
        self.assertEqual(kwargs["timeout"], 10.0)
        self.assertEqual(
            kwargs["headers"],
            {
                "Content-Type": "application/json",
                "X-API-Key": "secret-key",
                "X-Timestamp": str(timestamp),
                "X-Signature": hmac.new(
                    b"secret-key",
                    f"{timestamp}:{json.dumps([item], sort_keys=True)}".encode(),
                    hashlib.sha256,
                ).hexdigest(),
            },
        )


class SyncWorkerMainTests(unittest.IsolatedAsyncioTestCase):
    async def _run_main_once(
        self,
        *,
        blpop_results,
        target_url="https://peer.example/",
        api_key="sync-key",
        send_side_effect=None,
        send_return_value=None,
    ):
        fake_redis = FakeRedis(blpop_results)
        fake_settings = SimpleNamespace(redis_host="redis", redis_port=6379, sync_api_key=api_key)
        fake_client = FakeAsyncClient()
        send_mock = AsyncMock(side_effect=send_side_effect, return_value=send_return_value)
        sleep_mock = AsyncMock()

        with patch("core.sync_worker.redis.Redis", return_value=fake_redis), patch(
            "core.sync_worker.settings", fake_settings
        ), patch("core.sync_worker.default_peer_server_url", return_value=target_url), patch(
            "core.sync_worker.httpx.AsyncClient", return_value=fake_client
        ), patch("core.sync_worker.send_sync_item", send_mock), patch(
            "core.sync_worker.asyncio.sleep", sleep_mock
        ):
            with self.assertRaises(asyncio.CancelledError):
                await sync_worker.main()

        return fake_redis, send_mock, sleep_mock

    async def test_main_skips_invalid_json_payload(self):
        fake_redis, send_mock, sleep_mock = await self._run_main_once(
            blpop_results=[("sync:outbound", "not-json"), asyncio.CancelledError()]
        )

        send_mock.assert_not_awaited()
        sleep_mock.assert_not_awaited()
        self.assertEqual(fake_redis.rpush_calls, [])

    async def test_main_requeues_when_sync_config_missing(self):
        payload = json.dumps({"hash": "abc"})
        fake_redis, send_mock, sleep_mock = await self._run_main_once(
            blpop_results=[("sync:outbound", payload), asyncio.CancelledError()],
            target_url=None,
            api_key=None,
        )

        send_mock.assert_not_awaited()
        self.assertEqual(fake_redis.rpush_calls, [("sync:retry", payload)])
        sleep_mock.assert_awaited_once_with(30)

    async def test_main_normalizes_trailing_slash_and_does_not_requeue_success(self):
        payload = json.dumps({"hash": "abc"})
        response = SimpleNamespace(status_code=200, text="ok")
        fake_redis, send_mock, sleep_mock = await self._run_main_once(
            blpop_results=[("sync:outbound", payload), asyncio.CancelledError()],
            target_url="https://peer.example/",
            send_return_value=response,
        )

        send_mock.assert_awaited_once()
        _, args, _ = send_mock.mock_calls[0]
        self.assertEqual(args[2], "https://peer.example")
        self.assertEqual(fake_redis.rpush_calls, [])
        sleep_mock.assert_not_awaited()

    async def test_main_requeues_non_200_response(self):
        payload = json.dumps({"hash": "abc"})
        response = SimpleNamespace(status_code=500, text="boom")
        fake_redis, send_mock, sleep_mock = await self._run_main_once(
            blpop_results=[("sync:outbound", payload), asyncio.CancelledError()],
            send_return_value=response,
        )

        send_mock.assert_awaited_once()
        self.assertEqual(fake_redis.rpush_calls, [("sync:retry", payload)])
        sleep_mock.assert_awaited_once_with(1)

    async def test_main_requeues_request_errors(self):
        payload = json.dumps({"hash": "abc"})
        request_error = httpx.RequestError(
            "network down",
            request=httpx.Request("POST", "https://peer.example/api/sync/receive"),
        )
        fake_redis, send_mock, sleep_mock = await self._run_main_once(
            blpop_results=[("sync:retry", payload), asyncio.CancelledError()],
            send_side_effect=request_error,
        )

        send_mock.assert_awaited_once()
        self.assertEqual(fake_redis.rpush_calls, [("sync:retry", payload)])
        sleep_mock.assert_awaited_once_with(5)

    async def test_main_ignores_empty_blpop_results(self):
        fake_redis, send_mock, sleep_mock = await self._run_main_once(
            blpop_results=[None, asyncio.CancelledError()]
        )

        self.assertEqual(len(fake_redis.blpop_calls), 2)
        send_mock.assert_not_awaited()
        sleep_mock.assert_not_awaited()
        self.assertEqual(fake_redis.rpush_calls, [])


if __name__ == "__main__":
    unittest.main()