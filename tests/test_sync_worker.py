import asyncio
import hashlib
import hmac
import json
import unittest
from datetime import datetime
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


class FakeResponse:
    _missing = object()

    def __init__(self, status_code=200, text="", json_payload=_missing, headers=None):
        self.status_code = status_code
        self.text = text
        self.content = text.encode()
        self.headers = headers or {"content-type": "application/json"}
        self._json_payload = json_payload

    def json(self):
        if self._json_payload is self._missing:
            raise ValueError("invalid json")
        return self._json_payload


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class FakeExecuteResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return FakeScalarResult(self.value)


class FakeDBSession:
    def __init__(self, value):
        self.value = value
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement):
        self.statements.append(statement)
        return FakeExecuteResult(self.value)


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


class ChangeLogPayloadTests(unittest.TestCase):
    def test_change_log_entry_to_sync_item_includes_change_log_id_and_decoded_data(self):
        timestamp = datetime(2026, 1, 2, 3, 4, 5)
        entry = SimpleNamespace(
            id=77,
            operation="INSERT",
            table_name="offers",
            record_id=12,
            data='{"id":12,"status":"active"}',
            hash="hash-77",
            timestamp=timestamp,
        )

        item = sync_worker.change_log_entry_to_sync_item(entry)

        self.assertEqual(
            item,
            {
                "type": "db_change",
                "operation": "INSERT",
                "table": "offers",
                "id": 12,
                "data": {"id": 12, "status": "active"},
                "hash": "hash-77",
                "timestamp": timestamp.timestamp(),
                "change_log_id": 77,
                "sync_meta": {
                    "aggregate_table": "offers",
                    "aggregate_id": "12",
                    "aggregate_db_id": 12,
                    "authority_server": None,
                    "operation": "INSERT",
                    "authoritative_version": None,
                    "event_sequence": 77,
                    "outbox_id": 77,
                    "command_idempotency_id": None,
                },
            },
        )

    def test_change_log_entry_to_sync_item_includes_public_identity_when_available(self):
        timestamp = datetime(2026, 1, 2, 3, 4, 5)
        entry = SimpleNamespace(
            id=78,
            operation="UPDATE",
            table_name="offers",
            record_id=12,
            data={"id": 12, "offer_public_id": "ofr_12", "status": "active"},
            hash="hash-78",
            timestamp=timestamp,
        )

        item = sync_worker.change_log_entry_to_sync_item(entry)

        self.assertEqual(
            item["public_identity"],
            {
                "table": "offers",
                "kind": "offer_public_id",
                "value": "ofr_12",
                "record_id": 12,
            },
        )
        self.assertEqual(item["sync_meta"]["aggregate_id"], "ofr_12")


class ChangeLogDrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_next_unsynced_change_log_item_reads_committed_row(self):
        timestamp = datetime(2026, 1, 2, 3, 4, 5)
        entry = SimpleNamespace(
            id=88,
            operation="UPDATE",
            table_name="trades",
            record_id=42,
            data={"id": 42, "status": "confirmed"},
            hash="hash-88",
            timestamp=timestamp,
        )
        fake_session = FakeDBSession(entry)

        with patch("core.db.AsyncSessionLocal", return_value=fake_session):
            item = await sync_worker.fetch_next_unsynced_change_log_item()

        self.assertEqual(item["change_log_id"], 88)
        self.assertEqual(item["table"], "trades")
        self.assertEqual(item["data"], {"id": 42, "status": "confirmed"})
        self.assertEqual(len(fake_session.statements), 1)


class SyncWorkerMainTests(unittest.IsolatedAsyncioTestCase):
    async def _run_main_once(
        self,
        *,
        blpop_results,
        target_url="https://peer.example/",
        api_key="sync-key",
        send_side_effect=None,
        send_return_value=None,
        marker_side_effect=None,
        marker_return_value=1,
        fetch_return_value=None,
        fetch_side_effect=None,
    ):
        fake_redis = FakeRedis(blpop_results)
        fake_settings = SimpleNamespace(redis_host="redis", redis_port=6379, sync_api_key=api_key)
        fake_client = FakeAsyncClient()
        send_mock = AsyncMock(side_effect=send_side_effect, return_value=send_return_value)
        marker_mock = AsyncMock(side_effect=marker_side_effect, return_value=marker_return_value)
        fetch_mock = AsyncMock(side_effect=fetch_side_effect, return_value=fetch_return_value)
        sleep_mock = AsyncMock()

        with patch("core.sync_worker.redis.Redis", return_value=fake_redis), patch(
            "core.sync_worker.settings", fake_settings
        ), patch("core.sync_worker.default_peer_server_url", return_value=target_url), patch(
            "core.sync_worker.httpx.AsyncClient", return_value=fake_client
        ), patch("core.sync_worker.send_sync_item", send_mock), patch(
            "core.sync_worker.mark_change_log_delivered", marker_mock
        ), patch(
            "core.sync_worker.fetch_next_unsynced_change_log_item", fetch_mock
        ), patch(
            "core.sync_worker.asyncio.sleep", sleep_mock
        ):
            with self.assertRaises(asyncio.CancelledError):
                await sync_worker.main()

        self.fetch_mock = fetch_mock
        return fake_redis, send_mock, sleep_mock, marker_mock

    async def test_main_skips_invalid_json_payload(self):
        raw_payload = "not-json token=unsafe 09123456789"
        with patch("core.sync_worker.logger") as logger_mock:
            fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
                blpop_results=[("sync:outbound", raw_payload), asyncio.CancelledError()]
            )

        send_mock.assert_not_awaited()
        marker_mock.assert_not_awaited()
        sleep_mock.assert_not_awaited()
        self.assertEqual(fake_redis.rpush_calls, [])
        rendered_log_call = repr(logger_mock.error.call_args)
        self.assertNotIn(raw_payload, rendered_log_call)
        self.assertNotIn("unsafe", rendered_log_call)
        self.assertNotIn("09123456789", rendered_log_call)
        self.assertIn("payload_sha256", rendered_log_call)

    async def test_main_requeues_when_sync_config_missing(self):
        payload = json.dumps({"hash": "abc"})
        fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
            blpop_results=[("sync:outbound", payload), asyncio.CancelledError()],
            target_url=None,
            api_key=None,
        )

        send_mock.assert_not_awaited()
        marker_mock.assert_not_awaited()
        self.assertEqual(fake_redis.rpush_calls, [("sync:retry", payload)])
        sleep_mock.assert_awaited_once_with(30)

    async def test_main_normalizes_trailing_slash_and_does_not_requeue_success(self):
        payload = json.dumps({"hash": "abc", "change_log_id": 9})
        response = FakeResponse(200, '{"status":"success","processed":1,"errors":0}', {"status": "success", "processed": 1, "errors": 0})
        fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
            blpop_results=[("sync:outbound", payload), asyncio.CancelledError()],
            target_url="https://peer.example/",
            send_return_value=response,
        )

        send_mock.assert_awaited_once()
        _, args, _ = send_mock.mock_calls[0]
        self.assertEqual(args[2], "https://peer.example")
        marker_mock.assert_awaited_once_with({"hash": "abc", "change_log_id": 9})
        self.assertEqual(fake_redis.rpush_calls, [])
        sleep_mock.assert_not_awaited()

    async def test_main_requeues_non_200_response(self):
        payload = json.dumps({"hash": "abc"})
        response = FakeResponse(500, "boom token=unsafe 09123456789", {"status": "error", "errors": 1})
        with patch("core.job_logging.record_job_run") as record_job_run, patch(
            "core.sync_worker.logger"
        ) as logger_mock:
            fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
                blpop_results=[("sync:outbound", payload), asyncio.CancelledError()],
                send_return_value=response,
            )

        send_mock.assert_awaited_once()
        marker_mock.assert_not_awaited()
        self.assertEqual(fake_redis.rpush_calls, [("sync:retry", payload)])
        sleep_mock.assert_awaited_once_with(1)
        record_job_run.assert_called_once()
        self.assertEqual(record_job_run.call_args.kwargs["job_name"], "sync_worker")
        self.assertEqual(record_job_run.call_args.kwargs["result"], "failure")
        rendered_log_call = repr(logger_mock.error.call_args)
        self.assertNotIn(response.text, rendered_log_call)
        self.assertNotIn("unsafe", rendered_log_call)
        self.assertNotIn("09123456789", rendered_log_call)
        self.assertIn("peer_response_sha256", rendered_log_call)

    async def test_main_requeues_request_errors(self):
        payload = json.dumps({"hash": "abc"})
        request_error = httpx.RequestError(
            "network down",
            request=httpx.Request("POST", "https://peer.example/api/sync/receive"),
        )
        with patch("core.job_logging.record_job_run") as record_job_run:
            fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
                blpop_results=[("sync:retry", payload), asyncio.CancelledError()],
                send_side_effect=request_error,
            )

        send_mock.assert_awaited_once()
        marker_mock.assert_not_awaited()
        self.assertEqual(fake_redis.rpush_calls, [("sync:retry", payload)])
        sleep_mock.assert_awaited_once_with(5)
        record_job_run.assert_called_once()
        self.assertEqual(record_job_run.call_args.kwargs["job_name"], "sync_worker")
        self.assertEqual(record_job_run.call_args.kwargs["result"], "failure")

    async def test_main_ignores_empty_blpop_results(self):
        fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
            blpop_results=[None, asyncio.CancelledError()]
        )

        self.assertEqual(len(fake_redis.blpop_calls), 2)
        send_mock.assert_not_awaited()
        marker_mock.assert_not_awaited()
        self.fetch_mock.assert_awaited_once()
        sleep_mock.assert_not_awaited()
        self.assertEqual(fake_redis.rpush_calls, [])
        self.assertEqual(fake_redis.blpop_calls[0][0], ("sync:outbound", "sync:retry"))
        self.assertEqual(fake_redis.blpop_calls[1][0], ("sync:retry", "sync:outbound"))

    async def test_main_drains_committed_change_log_when_redis_has_no_wakeup(self):
        item = {
            "type": "db_change",
            "operation": "INSERT",
            "table": "offers",
            "id": 5,
            "data": {"id": 5},
            "hash": "abc",
            "timestamp": 1700000000,
            "change_log_id": 44,
        }
        response = FakeResponse(
            200,
            '{"status":"success","processed":1,"errors":0}',
            {"status": "success", "processed": 1, "errors": 0},
        )

        fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
            blpop_results=[None, asyncio.CancelledError()],
            fetch_return_value=item,
            send_return_value=response,
        )

        self.fetch_mock.assert_awaited_once()
        send_mock.assert_awaited_once()
        self.assertEqual(send_mock.await_args.args[1], item)
        marker_mock.assert_awaited_once_with(item)
        self.assertEqual(fake_redis.rpush_calls, [])
        sleep_mock.assert_not_awaited()

    async def test_main_keeps_db_sourced_change_log_unsynced_on_peer_rejection(self):
        item = {
            "type": "db_change",
            "operation": "UPDATE",
            "table": "offers",
            "id": 5,
            "data": {"id": 5},
            "hash": "abc",
            "timestamp": 1700000000,
            "change_log_id": 44,
        }
        response = FakeResponse(
            200,
            '{"status":"partial","processed":0,"errors":1}',
            {"status": "partial", "processed": 0, "errors": 1},
        )

        fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
            blpop_results=[None, asyncio.CancelledError()],
            fetch_return_value=item,
            send_return_value=response,
        )

        send_mock.assert_awaited_once()
        marker_mock.assert_not_awaited()
        self.assertEqual(fake_redis.rpush_calls, [])
        sleep_mock.assert_awaited_once_with(1)

    async def test_main_logs_and_retries_unexpected_loop_errors(self):
        fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
            blpop_results=[RuntimeError("loop exploded"), asyncio.CancelledError()]
        )

        self.assertEqual(len(fake_redis.blpop_calls), 2)
        send_mock.assert_not_awaited()
        marker_mock.assert_not_awaited()
        sleep_mock.assert_awaited_once_with(5)

    async def test_main_requeues_200_partial_response_without_marking_synced(self):
        payload = json.dumps({"hash": "abc", "change_log_id": 9})
        response = FakeResponse(
            200,
            '{"status":"partial","processed":0,"errors":1}',
            {
                "status": "partial",
                "processed": 0,
                "errors": 1,
                "error_items": [{"table": "mystery", "record_id": 8, "reason": "unregistered_table"}],
            },
        )
        fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
            blpop_results=[("sync:outbound", payload), asyncio.CancelledError()],
            send_return_value=response,
        )

        send_mock.assert_awaited_once()
        marker_mock.assert_not_awaited()
        self.assertEqual(fake_redis.rpush_calls, [("sync:retry", payload)])
        sleep_mock.assert_awaited_once_with(1)

    async def test_main_requeues_when_marker_fails_after_peer_acceptance(self):
        payload = json.dumps({"hash": "abc", "change_log_id": 9})
        response = FakeResponse(200, '{"status":"success","processed":1,"errors":0}', {"status": "success", "processed": 1, "errors": 0})
        fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
            blpop_results=[("sync:outbound", payload), asyncio.CancelledError()],
            send_return_value=response,
            marker_side_effect=RuntimeError("db down"),
        )

        send_mock.assert_awaited_once()
        marker_mock.assert_awaited_once()
        self.assertEqual(fake_redis.rpush_calls, [("sync:retry", payload)])
        sleep_mock.assert_awaited_once_with(1)


if __name__ == "__main__":
    unittest.main()
