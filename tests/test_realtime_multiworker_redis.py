import asyncio
import json
import multiprocessing
import os
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

import redis


def _guarded_redis_url() -> str:
    value = str(os.getenv("MARKET_STAGE14_TEST_REDIS_URL", "")).strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"redis", "rediss"}:
        raise RuntimeError("Stage 14 Redis test requires a Redis URL")
    if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.path != "/14":
        raise RuntimeError("Stage 14 Redis test requires local scratch database 14")
    return value


REDIS_URL = _guarded_redis_url()
WORKER_STARTUP_TIMEOUT_SECONDS = 30


def _listener_process(redis_url: str, ready, result) -> None:
    async def run() -> None:
        from api.routers import realtime

        original_redis_class = realtime.redis.Redis
        worker_pool = realtime.redis.ConnectionPool.from_url(redis_url)
        real_client = original_redis_class(connection_pool=worker_pool)
        received = asyncio.Event()
        sent = []

        class PubSubWrapper:
            def __init__(self, wrapped):
                self.wrapped = wrapped

            async def subscribe(self, *channels):
                await self.wrapped.subscribe(*channels)
                ready.send(True)

            async def get_message(self, **kwargs):
                return await self.wrapped.get_message(**kwargs)

        class RedisWrapper:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                # redis-py 5.0 exposes the async close operation as close();
                # newer versions also provide aclose(). Match the repository's
                # supported dependency instead of making the proof version-specific.
                await real_client.close()
                return False

            def pubsub(self):
                return PubSubWrapper(real_client.pubsub())

        class Socket:
            async def send_json(self, payload):
                if payload.get("type") == "offer:updated":
                    sent.append(payload)
                    received.set()

        with patch.object(realtime.redis, "Redis", return_value=RedisWrapper()):
            task = asyncio.create_task(realtime.listen_redis_events(Socket()))
            try:
                await asyncio.wait_for(received.wait(), timeout=5)
                await asyncio.sleep(0.3)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await worker_pool.disconnect()
        result.send(sent)

    try:
        asyncio.run(run())
    except Exception as exc:
        result.send({"error": f"{type(exc).__name__}:{exc}"})


class RealtimeMultiworkerRedisSafetyTests(unittest.TestCase):
    def test_rejects_nonlocal_or_shared_redis_target(self):
        with patch.dict(
            os.environ,
            {"MARKET_STAGE14_TEST_REDIS_URL": "redis://redis.example/0"},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "local scratch database 14"):
                _guarded_redis_url()


@unittest.skipUnless(REDIS_URL, "set MARKET_STAGE14_TEST_REDIS_URL")
class RealtimeMultiworkerRedisTests(unittest.TestCase):
    def test_two_worker_processes_deliver_one_copy_per_connection(self):
        client = redis.Redis.from_url(REDIS_URL)
        client.flushdb()
        context = multiprocessing.get_context("spawn")
        processes = []
        controls = []
        try:
            for _ in range(2):
                ready_parent, ready_child = context.Pipe(duplex=False)
                result_parent, result_child = context.Pipe(duplex=False)
                process = context.Process(
                    target=_listener_process,
                    args=(REDIS_URL, ready_child, result_child),
                )
                process.start()
                processes.append(process)
                controls.append((ready_parent, result_parent))

            for ready, _result in controls:
                # Spawn imports the full application in each process. Cold CI
                # runners can spend well over eight seconds on those imports.
                self.assertTrue(
                    ready.poll(WORKER_STARTUP_TIMEOUT_SECONDS),
                    "worker did not subscribe to Redis",
                )
                self.assertTrue(ready.recv())

            payload = json.dumps({"id": 7, "_realtime_event_id": "stage14-process-event"})
            client.publish("events:offer:updated", payload)
            client.publish("events:offer:updated", payload)

            results = []
            for _ready, result in controls:
                self.assertTrue(result.poll(8), "worker did not receive Redis event")
                results.append(result.recv())
            for worker_result in results:
                self.assertIsInstance(worker_result, list, worker_result)
                self.assertEqual(len(worker_result), 1)
                self.assertEqual(worker_result[0]["event_id"], "stage14-process-event")
        finally:
            for process in processes:
                process.join(timeout=3)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=3)
            client.flushdb()
            client.close()


if __name__ == "__main__":
    unittest.main()
