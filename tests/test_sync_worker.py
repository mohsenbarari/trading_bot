import asyncio
import hashlib
import hmac
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

from core import sync_worker
from core.sync_protocol import build_sync_protocol_metadata


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
        self.values = list(value) if isinstance(value, list) else None
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement):
        self.statements.append(statement)
        if self.values is not None:
            return FakeExecuteResult(self.values.pop(0))
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


class PeerResponsePolicyTests(unittest.TestCase):
    def test_terminal_source_authority_tables_share_receiver_authority_set(self):
        from api.routers.sync import IRAN_AUTHORITATIVE_SYNC_TABLES as receiver_authority_tables

        self.assertIs(sync_worker.TERMINAL_SOURCE_AUTHORITY_REJECTION_TABLES, receiver_authority_tables)

    def test_policy_forbidden_no_sync_response_is_detected(self):
        response = FakeResponse(
            200,
            '{"status":"partial","processed":0,"errors":1}',
            {
                "status": "partial",
                "processed": 0,
                "errors": 1,
                "error_items": [
                    {"table": "chat_members", "record_id": 12, "reason": "policy_forbidden:no-sync"}
                ],
            },
        )

        self.assertTrue(sync_worker.peer_response_is_policy_forbidden_no_sync(response))
        self.assertTrue(sync_worker.peer_response_is_terminal_policy_rejection(response))
        self.assertTrue(
            sync_worker.peer_response_is_terminal_policy_rejection_for_item(
                response,
                {"table": "chat_members", "id": 12},
            )
        )

    def test_source_authority_forbidden_response_is_terminal_rejection(self):
        response = FakeResponse(
            200,
            '{"status":"partial","processed":0,"errors":1}',
            {
                "status": "partial",
                "processed": 0,
                "errors": 1,
                "error_items": [
                    {
                        "table": "market_runtime_state",
                        "record_id": 1,
                        "reason": "source_authority_forbidden:foreign",
                    }
                ],
            },
        )

        self.assertFalse(sync_worker.peer_response_is_policy_forbidden_no_sync(response))
        self.assertTrue(sync_worker.peer_response_is_terminal_policy_rejection(response))
        self.assertTrue(
            sync_worker.peer_response_is_terminal_policy_rejection_for_item(
                response,
                {"table": "market_runtime_state", "id": 1},
            )
        )

    def test_source_authority_terminal_rejection_requires_allowed_table_and_identity_match(self):
        allowed_response = FakeResponse(
            200,
            '{"status":"partial","processed":0,"errors":1}',
            {
                "status": "partial",
                "processed": 0,
                "errors": 1,
                "error_items": [
                    {
                        "table": "market_runtime_state",
                        "record_id": 1,
                        "reason": "source_authority_forbidden:foreign",
                    }
                ],
            },
        )
        non_authority_table_response = FakeResponse(
            200,
            '{"status":"partial","processed":0,"errors":1}',
            {
                "status": "partial",
                "processed": 0,
                "errors": 1,
                "error_items": [
                    {
                        "table": "offers",
                        "record_id": 1,
                        "reason": "source_authority_forbidden:foreign",
                    }
                ],
            },
        )

        self.assertFalse(
            sync_worker.peer_response_is_terminal_policy_rejection_for_item(
                allowed_response,
                {"table": "offers", "id": 1},
            )
        )
        self.assertFalse(
            sync_worker.peer_response_is_terminal_policy_rejection_for_item(
                allowed_response,
                {"table": "market_runtime_state", "id": 2},
            )
        )
        self.assertFalse(sync_worker.peer_response_is_terminal_policy_rejection(non_authority_table_response))

    def test_policy_forbidden_no_sync_response_requires_exact_single_rejection(self):
        success_response = FakeResponse(
            200,
            '{"status":"success","processed":1,"errors":0}',
            {"status": "success", "processed": 1, "errors": 0},
        )
        mixed_response = FakeResponse(
            200,
            '{"status":"partial","processed":1,"errors":1}',
            {
                "status": "partial",
                "processed": 1,
                "errors": 1,
                "error_items": [
                    {"table": "chat_members", "record_id": 12, "reason": "policy_forbidden:no-sync"}
                ],
            },
        )

        self.assertFalse(sync_worker.peer_response_is_policy_forbidden_no_sync(success_response))
        self.assertFalse(sync_worker.peer_response_is_policy_forbidden_no_sync(mixed_response))
        self.assertFalse(sync_worker.peer_response_is_terminal_policy_rejection(success_response))
        self.assertFalse(sync_worker.peer_response_is_terminal_policy_rejection(mixed_response))


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
                "sync_protocol": build_sync_protocol_metadata(),
                "sync_meta": {
                    "aggregate_table": "offers",
                    "aggregate_id": "12",
                    "aggregate_db_id": 12,
                    "source_server": "foreign",
                    "source_sequence": 77,
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

    def test_change_log_entry_to_sync_item_sanitizes_legacy_sensitive_user_payload(self):
        timestamp = datetime(2026, 1, 2, 3, 4, 5)
        entry = SimpleNamespace(
            id=79,
            operation="UPDATE",
            table_name="users",
            record_id=7,
            data={
                "id": 7,
                "mobile_number": "09120000000",
                "admin_password_hash": "bcrypt-secret",
                "must_change_password": True,
                "avatar_file_id": "chat-file-user",
            },
            hash="hash-79",
            timestamp=timestamp,
        )

        item = sync_worker.change_log_entry_to_sync_item(entry)

        self.assertEqual(item["data"]["mobile_number"], "09120000000")
        self.assertNotIn("admin_password_hash", item["data"])
        self.assertNotIn("must_change_password", item["data"])
        self.assertNotIn("avatar_file_id", item["data"])


def make_offer_snapshot(**overrides):
    data = {
        "id": 12,
        "offer_public_id": "ofr_12",
        "version_id": 4,
        "user_id": 5,
        "actor_user_id": None,
        "home_server": "iran",
        "offer_type": SimpleNamespace(value="sell"),
        "commodity_id": 2,
        "quantity": 40,
        "remaining_quantity": 0,
        "price": 1000,
        "is_wholesale": True,
        "lot_sizes": None,
        "original_lot_sizes": None,
        "expire_reason": "manual",
        "expired_by_user_id": 5,
        "expired_by_actor_user_id": 5,
        "expire_source_surface": "webapp",
        "expire_source_server": "iran",
        "notes": None,
        "status": SimpleNamespace(value="expired"),
        "channel_message_id": 700,
        "republished_offer_id": None,
        "created_at": datetime(2026, 1, 2, 3, 0, 0),
        "updated_at": datetime(2026, 1, 2, 3, 5, 0),
        "expired_at": datetime(2026, 1, 2, 3, 5, 0),
        "idempotency_key": "offer-create-12",
        "archived": False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class ChangeLogDrainTests(unittest.IsolatedAsyncioTestCase):
    def test_outbound_table_priority_sends_trades_before_offer_requests(self):
        self.assertLess(
            sync_worker.SYNC_OUTBOUND_TABLE_PRIORITY.index("trades"),
            sync_worker.SYNC_OUTBOUND_TABLE_PRIORITY.index("offer_requests"),
        )
        self.assertLess(
            sync_worker.SYNC_OUTBOUND_TABLE_PRIORITY.index("trades"),
            sync_worker.SYNC_OUTBOUND_TABLE_PRIORITY.index("trade_delivery_receipts"),
        )
        self.assertLess(
            sync_worker.SYNC_OUTBOUND_TABLE_PRIORITY.index("telegram_admin_broadcasts"),
            sync_worker.SYNC_OUTBOUND_TABLE_PRIORITY.index("telegram_admin_broadcast_receipts"),
        )
        self.assertLess(
            sync_worker.SYNC_OUTBOUND_TABLE_PRIORITY.index("telegram_admin_broadcast_receipts"),
            sync_worker.SYNC_OUTBOUND_TABLE_PRIORITY.index("notifications"),
        )

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
        self.assertEqual(item["sync_protocol"], build_sync_protocol_metadata())
        self.assertEqual(item["data"], {"id": 42, "status": "confirmed"})
        self.assertEqual(len(fake_session.statements), 1)

    async def test_offer_change_log_replay_uses_original_committed_payload(self):
        timestamp = datetime(2026, 1, 2, 3, 4, 5)
        entry = SimpleNamespace(
            id=90,
            operation="INSERT",
            table_name="offers",
            record_id=12,
            data={"id": 12, "offer_public_id": "ofr_12", "status": "active", "version_id": 1},
            hash="hash-90",
            timestamp=timestamp,
        )
        latest_offer = make_offer_snapshot(status=SimpleNamespace(value="expired"))
        fake_session = FakeDBSession([entry, latest_offer])

        with patch("core.db.AsyncSessionLocal", return_value=fake_session):
            item = await sync_worker.fetch_next_unsynced_change_log_item()

        self.assertEqual(item["change_log_id"], 90)
        self.assertEqual(item["operation"], "INSERT")
        self.assertEqual(item["hash"], "hash-90")
        self.assertEqual(item["id"], 12)
        self.assertEqual(item["data"]["offer_public_id"], "ofr_12")
        self.assertEqual(item["data"]["status"], "active")
        self.assertEqual(item["data"]["version_id"], 1)
        self.assertNotIn("remaining_quantity", item["data"])
        self.assertEqual(item["sync_meta"]["authoritative_version"], 1)
        self.assertEqual(item["sync_meta"]["event_sequence"], 1)
        self.assertEqual(item["sync_meta"]["outbox_id"], 90)
        self.assertEqual(item["public_identity"]["value"], "ofr_12")
        self.assertEqual(len(fake_session.statements), 1)

    async def test_offer_change_log_replay_does_not_rebuild_same_sequence_from_current_state(self):
        timestamp = datetime(2026, 1, 2, 3, 4, 5)
        entry = SimpleNamespace(
            id=91,
            operation="INSERT",
            table_name="offers",
            record_id=12,
            data={"id": 12, "offer_public_id": "ofr_12", "status": "active", "version_id": 1},
            hash="hash-91",
            timestamp=timestamp,
        )
        latest_offer = make_offer_snapshot(
            status=SimpleNamespace(value="completed"),
            expire_reason=None,
            expired_at=None,
            expired_by_user_id=None,
            expired_by_actor_user_id=None,
            expire_source_surface=None,
            expire_source_server=None,
        )
        fake_session = FakeDBSession([entry, latest_offer])

        with patch("core.db.AsyncSessionLocal", return_value=fake_session):
            item = await sync_worker.fetch_next_unsynced_change_log_item()

        self.assertEqual(item["change_log_id"], 91)
        self.assertEqual(item["hash"], "hash-91")
        self.assertEqual(item["data"]["status"], "active")
        self.assertEqual(item["data"]["version_id"], 1)
        self.assertNotIn("remaining_quantity", item["data"])
        self.assertEqual(item["sync_meta"]["authoritative_version"], 1)
        self.assertEqual(item["sync_meta"]["event_sequence"], 1)
        self.assertEqual(item["sync_meta"]["outbox_id"], 91)
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
        sync_verify_tls=True,
        sync_ca_bundle=None,
    ):
        fake_redis = FakeRedis(blpop_results)
        fake_settings = SimpleNamespace(
            redis_host="redis",
            redis_port=6379,
            sync_api_key=api_key,
            sync_verify_tls=sync_verify_tls,
            sync_ca_bundle=sync_ca_bundle,
            environment="production",
        )
        fake_client = FakeAsyncClient()
        client_ctor = Mock(return_value=fake_client)
        send_mock = AsyncMock(side_effect=send_side_effect, return_value=send_return_value)
        marker_mock = AsyncMock(side_effect=marker_side_effect, return_value=marker_return_value)
        fetch_mock = AsyncMock(side_effect=fetch_side_effect, return_value=fetch_return_value)
        sleep_mock = AsyncMock()

        with patch("core.sync_worker.redis.Redis", return_value=fake_redis), patch(
            "core.sync_worker.settings", fake_settings
        ), patch("core.sync_worker.default_peer_server_url", return_value=target_url), patch(
            "core.sync_worker.httpx.AsyncClient", client_ctor
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
        self.client_ctor = client_ctor
        return fake_redis, send_mock, sleep_mock, marker_mock

    async def test_main_skips_invalid_json_payload(self):
        raw_payload = "not-json token=unsafe 09123456789"
        with patch("core.sync_worker.logger") as logger_mock:
            fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
                blpop_results=[("sync:retry", raw_payload), asyncio.CancelledError()]
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
            blpop_results=[("sync:retry", payload), asyncio.CancelledError()],
            target_url=None,
            api_key=None,
        )

        send_mock.assert_not_awaited()
        marker_mock.assert_not_awaited()
        self.assertEqual(
            [(queue, json.loads(queued_payload)) for queue, queued_payload in fake_redis.rpush_calls],
            [("sync:retry", json.loads(payload))],
        )
        sleep_mock.assert_awaited_once_with(30)

    async def test_main_uses_sync_transport_ca_bundle(self):
        with patch("core.config.settings.sync_ca_bundle", "/etc/ssl/internal-ca.pem"):
            await self._run_main_once(
                blpop_results=[asyncio.CancelledError()],
            )

        self.assertEqual(self.client_ctor.call_args.kwargs["verify"], "/etc/ssl/internal-ca.pem")

    async def test_main_normalizes_trailing_slash_and_does_not_requeue_success(self):
        payload = json.dumps({"hash": "abc", "change_log_id": 9})
        response = FakeResponse(200, '{"status":"success","processed":1,"errors":0}', {"status": "success", "processed": 1, "errors": 0})
        fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
            blpop_results=[("sync:retry", payload), asyncio.CancelledError()],
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
                blpop_results=[("sync:retry", payload), asyncio.CancelledError()],
                send_return_value=response,
            )

        send_mock.assert_awaited_once()
        marker_mock.assert_not_awaited()
        self.assertEqual(
            [(queue, json.loads(queued_payload)) for queue, queued_payload in fake_redis.rpush_calls],
            [("sync:retry", json.loads(payload))],
        )
        sleep_mock.assert_awaited_once_with(1)
        record_job_run.assert_called_once()
        self.assertEqual(record_job_run.call_args.kwargs["job_name"], "sync_worker")
        self.assertEqual(record_job_run.call_args.kwargs["result"], "failure")
        rendered_log_call = repr(logger_mock.error.call_args)
        self.assertNotIn(response.text, rendered_log_call)
        self.assertNotIn("unsafe", rendered_log_call)
        self.assertNotIn("09123456789", rendered_log_call)
        self.assertIn("peer_response_sha256", rendered_log_call)

    async def test_main_drops_policy_forbidden_no_sync_without_requeue(self):
        payload = json.dumps({"hash": "abc", "table": "chat_members", "id": 12, "change_log_id": 99})
        response = FakeResponse(
            200,
            '{"status":"partial","processed":0,"errors":1}',
            {
                "status": "partial",
                "processed": 0,
                "errors": 1,
                "error_items": [
                    {"table": "chat_members", "record_id": 12, "reason": "policy_forbidden:no-sync"}
                ],
            },
        )
        fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
            blpop_results=[("sync:retry", payload), asyncio.CancelledError()],
            send_return_value=response,
        )

        send_mock.assert_awaited_once()
        marker_mock.assert_awaited_once_with(json.loads(payload))
        self.assertEqual(fake_redis.rpush_calls, [])
        sleep_mock.assert_not_awaited()

    async def test_main_requeues_terminal_rejection_when_peer_identity_does_not_match_item(self):
        payload = json.dumps(
            {"hash": "abc", "table": "market_runtime_state", "id": 1, "change_log_id": 100}
        )
        response = FakeResponse(
            200,
            '{"status":"partial","processed":0,"errors":1}',
            {
                "status": "partial",
                "processed": 0,
                "errors": 1,
                "error_items": [
                    {
                        "table": "market_runtime_state",
                        "record_id": 2,
                        "reason": "source_authority_forbidden:foreign",
                    }
                ],
            },
        )
        fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
            blpop_results=[("sync:retry", payload), asyncio.CancelledError()],
            send_return_value=response,
        )

        send_mock.assert_awaited_once()
        marker_mock.assert_not_awaited()
        self.assertEqual(
            [(queue, json.loads(queued_payload)) for queue, queued_payload in fake_redis.rpush_calls],
            [("sync:retry", json.loads(payload))],
        )
        sleep_mock.assert_awaited_once_with(1)

    async def test_main_drops_source_authority_forbidden_without_requeue(self):
        payload = json.dumps(
            {"hash": "abc", "table": "market_runtime_state", "id": 1, "change_log_id": 100}
        )
        response = FakeResponse(
            200,
            '{"status":"partial","processed":0,"errors":1}',
            {
                "status": "partial",
                "processed": 0,
                "errors": 1,
                "error_items": [
                    {
                        "table": "market_runtime_state",
                        "record_id": 1,
                        "reason": "source_authority_forbidden:foreign",
                    }
                ],
            },
        )
        with patch("core.sync_worker.record_sync_terminal_policy_rejection") as metric_mock:
            fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
                blpop_results=[("sync:retry", payload), asyncio.CancelledError()],
                send_return_value=response,
            )

        send_mock.assert_awaited_once()
        marker_mock.assert_awaited_once_with(json.loads(payload))
        metric_mock.assert_called_once_with(
            server_mode="foreign",
            table="market_runtime_state",
            reason="source_authority_forbidden:foreign",
        )
        self.assertEqual(fake_redis.rpush_calls, [])
        sleep_mock.assert_not_awaited()

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
        self.assertEqual(
            [(queue, json.loads(queued_payload)) for queue, queued_payload in fake_redis.rpush_calls],
            [("sync:retry", json.loads(payload))],
        )
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

    async def test_main_treats_outbound_payload_as_wakeup_and_sends_committed_change_log(self):
        stale_payload = json.dumps(
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
        committed_item = {
            "type": "db_change",
            "operation": "UPDATE",
            "table": "trades",
            "id": 5,
            "data": {"id": 5, "status": "confirmed"},
            "hash": "committed-hash",
            "timestamp": 1700000000,
            "change_log_id": 45,
        }
        response = FakeResponse(
            200,
            '{"status":"success","processed":1,"errors":0}',
            {"status": "success", "processed": 1, "errors": 0},
        )

        fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
            blpop_results=[("sync:outbound", stale_payload), asyncio.CancelledError()],
            fetch_return_value=committed_item,
            send_return_value=response,
        )

        self.fetch_mock.assert_awaited_once()
        send_mock.assert_awaited_once()
        self.assertEqual(send_mock.await_args.args[1], committed_item)
        marker_mock.assert_awaited_once_with(committed_item)
        self.assertEqual(fake_redis.rpush_calls, [])
        sleep_mock.assert_not_awaited()

    async def test_main_drops_outbound_wakeup_when_no_committed_change_log_exists(self):
        stale_payload = json.dumps({"hash": "precommit-hash", "change_log_id": 999})
        with patch("core.sync_worker.logger") as logger_mock:
            fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
                blpop_results=[("sync:outbound", stale_payload), asyncio.CancelledError()],
                fetch_return_value=None,
            )

        self.fetch_mock.assert_awaited_once()
        send_mock.assert_not_awaited()
        marker_mock.assert_not_awaited()
        self.assertEqual(fake_redis.rpush_calls, [])
        sleep_mock.assert_not_awaited()
        self.assertIn("job.item.outbound_wakeup_no_committed_change", repr(logger_mock.info.call_args))

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
            blpop_results=[("sync:retry", payload), asyncio.CancelledError()],
            send_return_value=response,
        )

        send_mock.assert_awaited_once()
        marker_mock.assert_not_awaited()
        self.assertEqual(
            [(queue, json.loads(queued_payload)) for queue, queued_payload in fake_redis.rpush_calls],
            [("sync:retry", json.loads(payload))],
        )
        sleep_mock.assert_awaited_once_with(1)

    async def test_main_retries_payload_without_refreshing_offer_state(self):
        stale_item = {
            "type": "db_change",
            "operation": "INSERT",
            "table": "offers",
            "id": 12,
            "data": {"id": 12, "offer_public_id": "ofr_12", "status": "active", "version_id": 1},
            "hash": "hash-12",
            "change_log_id": 90,
        }
        response = FakeResponse(500, "peer down", {"status": "error", "errors": 1})

        fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
            blpop_results=[("sync:retry", json.dumps(stale_item)), asyncio.CancelledError()],
            send_return_value=response,
        )

        send_mock.assert_awaited_once()
        self.assertEqual(send_mock.await_args.args[1], stale_item)
        marker_mock.assert_not_awaited()
        self.assertEqual(len(fake_redis.rpush_calls), 1)
        self.assertEqual(fake_redis.rpush_calls[0][0], "sync:retry")
        self.assertEqual(json.loads(fake_redis.rpush_calls[0][1])["data"]["status"], "active")
        sleep_mock.assert_awaited_once_with(1)

    async def test_main_requeues_when_marker_fails_after_peer_acceptance(self):
        payload = json.dumps({"hash": "abc", "change_log_id": 9})
        response = FakeResponse(200, '{"status":"success","processed":1,"errors":0}', {"status": "success", "processed": 1, "errors": 0})
        fake_redis, send_mock, sleep_mock, marker_mock = await self._run_main_once(
            blpop_results=[("sync:retry", payload), asyncio.CancelledError()],
            send_return_value=response,
            marker_side_effect=RuntimeError("db down"),
        )

        send_mock.assert_awaited_once()
        marker_mock.assert_awaited_once()
        self.assertEqual(
            [(queue, json.loads(queued_payload)) for queue, queued_payload in fake_redis.rpush_calls],
            [("sync:retry", json.loads(payload))],
        )
        sleep_mock.assert_awaited_once_with(1)


if __name__ == "__main__":
    unittest.main()
