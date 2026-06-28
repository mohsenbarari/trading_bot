import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from core import events, sync_worker
from core.sync_metadata import build_sync_metadata
from core.sync_parity import build_table_parity_snapshot, compare_parity_snapshots
from core.sync_registry import SyncPolicy, get_sync_registry_entry


class _FakeInsertResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeConnection:
    def __init__(self):
        self.info = {}
        self.execute = MagicMock(return_value=_FakeInsertResult(101))

    def get_execution_options(self):
        return {"is_sync": False}


class _FakeRedis:
    def __init__(self, blpop_results):
        self._blpop_results = list(blpop_results)
        self.blpop_calls = []
        self.rpush_calls = []

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


class _FakeAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeResponse:
    status_code = 200
    text = '{"status":"success","processed":1,"errors":0}'
    headers = {"content-type": "application/json"}
    content = text.encode()

    def json(self):
        return {"status": "success", "processed": 1, "errors": 0}


class SyncParityFollowupF0Tests(unittest.IsolatedAsyncioTestCase):
    def test_log_change_records_outbox_without_precommit_peer_push(self):
        connection = _FakeConnection()
        payload = {"id": 7, "offer_public_id": "ofr_f0", "status": "active"}

        with patch("core.events._get_sync_redis") as get_sync_redis, patch(
            "core.sync_push.push_sync_direct"
        ) as push_sync_direct:
            events.log_change(connection, "offers", 7, "INSERT", payload)

        connection.execute.assert_called_once()
        get_sync_redis.assert_not_called()
        push_sync_direct.assert_not_called()
        inserted_payload = json.loads(connection.execute.call_args.args[1]["data"])
        self.assertEqual(inserted_payload, payload)

    async def test_worker_uses_outbound_queue_only_as_wakeup_for_committed_change_log(self):
        stale_precommit_payload = json.dumps(
            {
                "type": "db_change",
                "operation": "INSERT",
                "table": "offers",
                "id": 999,
                "data": {"id": 999, "status": "precommit"},
                "hash": "precommit-hash",
                "change_log_id": 999,
            }
        )
        committed_change = {
            "type": "db_change",
            "operation": "UPDATE",
            "table": "trades",
            "id": 42,
            "data": {"id": 42, "status": "confirmed"},
            "hash": "committed-hash",
            "timestamp": 1700000000,
            "change_log_id": 4242,
        }
        fake_redis = _FakeRedis([("sync:outbound", stale_precommit_payload), asyncio.CancelledError()])
        fake_settings = SimpleNamespace(
            redis_host="redis",
            redis_port=6379,
            sync_api_key="sync-key",
            sync_verify_tls=True,
            sync_ca_bundle=None,
            environment="production",
        )
        send_mock = AsyncMock(return_value=_FakeResponse())
        marker_mock = AsyncMock(return_value=1)
        fetch_mock = AsyncMock(return_value=committed_change)
        sleep_mock = AsyncMock()

        with patch("core.sync_worker.redis.Redis", return_value=fake_redis), patch(
            "core.sync_worker.settings", fake_settings
        ), patch("core.sync_worker.default_peer_server_url", return_value="https://peer.example"), patch(
            "core.sync_worker.httpx.AsyncClient", Mock(return_value=_FakeAsyncClient())
        ), patch("core.sync_worker.send_sync_item", send_mock), patch(
            "core.sync_worker.mark_change_log_delivered", marker_mock
        ), patch(
            "core.sync_worker.fetch_next_unsynced_change_log_item", fetch_mock
        ), patch(
            "core.sync_worker.asyncio.sleep", sleep_mock
        ):
            with self.assertRaises(asyncio.CancelledError):
                await sync_worker.main()

        fetch_mock.assert_awaited_once()
        send_mock.assert_awaited_once()
        self.assertEqual(send_mock.await_args.args[1], committed_change)
        marker_mock.assert_awaited_once_with(committed_change)
        self.assertEqual(fake_redis.rpush_calls, [])
        sleep_mock.assert_not_awaited()

    def test_logical_sync_metadata_identities_do_not_collapse_distinct_rows(self):
        first_request = build_sync_metadata(
            "offer_requests",
            31,
            "INSERT",
            {
                "offer_public_id": "ofr_shared",
                "request_home_server": "foreign",
                "idempotency_key": "request-a",
            },
            change_log_id=201,
        )
        second_request = build_sync_metadata(
            "offer_requests",
            32,
            "INSERT",
            {
                "offer_public_id": "ofr_shared",
                "request_home_server": "foreign",
                "idempotency_key": "request-b",
            },
            change_log_id=202,
        )
        open_time_setting = build_sync_metadata(
            "trading_settings",
            0,
            "UPDATE",
            {"key": "market_open_time_local", "value": "10:00"},
            change_log_id=301,
        )
        close_time_setting = build_sync_metadata(
            "trading_settings",
            0,
            "UPDATE",
            {"key": "market_close_time_local", "value": "17:00"},
            change_log_id=302,
        )

        self.assertEqual(first_request["aggregate_id"], "foreign:request-a")
        self.assertEqual(second_request["aggregate_id"], "foreign:request-b")
        self.assertNotEqual(first_request["aggregate_id"], second_request["aggregate_id"])
        self.assertEqual(open_time_setting["aggregate_id"], "market_open_time_local")
        self.assertEqual(close_time_setting["aggregate_id"], "market_close_time_local")
        self.assertNotEqual(open_time_setting["aggregate_id"], close_time_setting["aggregate_id"])

    def test_truncated_parity_snapshot_is_incomplete_not_clean(self):
        local = {
            "status": "ok",
            "schema_version": 1,
            "mode": "deep",
            "tables": {
                "offers": build_table_parity_snapshot(
                    "offers",
                    [
                        {"id": 1, "offer_public_id": "ofr_1", "price": 100},
                        {"id": 2, "offer_public_id": "ofr_2", "price": 100},
                    ],
                    max_rows=1,
                )
            },
        }
        peer = {
            "status": "ok",
            "schema_version": 1,
            "mode": "deep",
            "tables": {
                "offers": build_table_parity_snapshot(
                    "offers",
                    [{"id": 1, "offer_public_id": "ofr_1", "price": 100}],
                )
            },
        }

        report = compare_parity_snapshots(local, peer)

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["severity_counts"]["incomplete"], 1)
        self.assertTrue(report["tables"]["offers"]["local_truncated"])

    def test_market_channel_notice_receipts_remain_foreign_local_no_sync(self):
        entry = get_sync_registry_entry("market_channel_notice_receipts")

        self.assertEqual(entry.policy, SyncPolicy.NO_SYNC)
        self.assertIn("foreign local", entry.authority)
