import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.sync import (
    SyncWatermarkDecision,
    _evaluate_sync_watermark,
    _sync_watermark_context_from_item,
    receive_sync_data,
)
from core.sync_metadata import build_sync_metadata


class ScalarFirstResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return SimpleNamespace(first=lambda: self._value)


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.execute_calls = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt, *args, **kwargs):
        self.execute_calls.append((stmt, args, kwargs))
        if self.execute_results:
            value = self.execute_results.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        return ScalarFirstResult(None)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    def begin_nested(self):
        class _Ctx:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Ctx()


def sync_item(sequence=10, *, payload_name="new"):
    return {
        "type": "db_change",
        "table": "users",
        "operation": "UPDATE",
        "id": 5,
        "data": {"id": 5, "full_name": payload_name},
        "change_log_id": sequence,
        "sync_meta": {
            "source_server": "foreign",
            "source_sequence": sequence,
            "aggregate_table": "users",
            "aggregate_id": "5",
        },
    }


class SyncRouterWatermarkTests(unittest.IsolatedAsyncioTestCase):
    def test_watermark_context_uses_source_sequence_and_aggregate_key(self):
        item = sync_item(12, payload_name="user-12")
        context = _sync_watermark_context_from_item(
            item,
            table="users",
            operation="UPDATE",
            record_id=5,
            data=item["data"],
        )

        self.assertIsNotNone(context)
        self.assertEqual(context.source_server, "foreign")
        self.assertEqual(context.source_sequence, 12)
        self.assertEqual(context.aggregate_table, "users")
        self.assertEqual(context.aggregate_key, "5")
        self.assertEqual(len(context.payload_hash), 64)

    def test_offer_request_watermark_context_uses_idempotency_key_per_logical_request(self):
        first_data = {
            "id": 31,
            "offer_public_id": "ofr_shared",
            "request_home_server": "foreign",
            "idempotency_key": "request-a",
        }
        second_data = {
            "id": 32,
            "offer_public_id": "ofr_shared",
            "request_home_server": "foreign",
            "idempotency_key": "request-b",
        }
        first = {
            "table": "offer_requests",
            "operation": "INSERT",
            "id": 31,
            "data": first_data,
            "change_log_id": 201,
            "sync_meta": build_sync_metadata("offer_requests", 31, "INSERT", first_data, change_log_id=201),
        }
        second = {
            "table": "offer_requests",
            "operation": "INSERT",
            "id": 32,
            "data": second_data,
            "change_log_id": 202,
            "sync_meta": build_sync_metadata("offer_requests", 32, "INSERT", second_data, change_log_id=202),
        }

        first_context = _sync_watermark_context_from_item(
            first,
            table="offer_requests",
            operation="INSERT",
            record_id=31,
            data=first_data,
        )
        second_context = _sync_watermark_context_from_item(
            second,
            table="offer_requests",
            operation="INSERT",
            record_id=32,
            data=second_data,
        )

        self.assertEqual(first_context.aggregate_key, "foreign:request-a")
        self.assertEqual(second_context.aggregate_key, "foreign:request-b")
        self.assertNotEqual(first_context.aggregate_key, second_context.aggregate_key)

    def test_trading_settings_watermark_context_uses_setting_key_per_logical_setting(self):
        first_data = {"key": "market_open_time_local", "value": "10:00"}
        second_data = {"key": "market_close_time_local", "value": "17:00"}
        first = {
            "table": "trading_settings",
            "operation": "UPDATE",
            "id": 0,
            "data": first_data,
            "change_log_id": 301,
            "sync_meta": build_sync_metadata("trading_settings", 0, "UPDATE", first_data, change_log_id=301),
        }
        second = {
            "table": "trading_settings",
            "operation": "UPDATE",
            "id": 0,
            "data": second_data,
            "change_log_id": 302,
            "sync_meta": build_sync_metadata("trading_settings", 0, "UPDATE", second_data, change_log_id=302),
        }

        first_context = _sync_watermark_context_from_item(
            first,
            table="trading_settings",
            operation="UPDATE",
            record_id=0,
            data=first_data,
        )
        second_context = _sync_watermark_context_from_item(
            second,
            table="trading_settings",
            operation="UPDATE",
            record_id=0,
            data=second_data,
        )

        self.assertEqual(first_context.aggregate_key, "market_open_time_local")
        self.assertEqual(second_context.aggregate_key, "market_close_time_local")
        self.assertNotEqual(first_context.aggregate_key, second_context.aggregate_key)

    async def test_watermark_decisions_cover_newer_stale_duplicate_and_conflict(self):
        item = sync_item(10)
        context = _sync_watermark_context_from_item(
            item,
            table="users",
            operation="UPDATE",
            record_id=5,
            data=item["data"],
        )

        self.assertEqual((await _evaluate_sync_watermark(FakeDB(), context)).action, "apply")

        stale_watermark = SimpleNamespace(last_source_sequence=20, last_payload_hash=context.payload_hash)
        stale_decision = await _evaluate_sync_watermark(FakeDB([ScalarFirstResult(None), ScalarFirstResult(stale_watermark)]), context)
        self.assertEqual(stale_decision, SyncWatermarkDecision("stale", "older_source_sequence"))

        duplicate_watermark = SimpleNamespace(last_source_sequence=10, last_payload_hash=context.payload_hash)
        duplicate_decision = await _evaluate_sync_watermark(
            FakeDB([ScalarFirstResult(None), ScalarFirstResult(duplicate_watermark)]),
            context,
        )
        self.assertEqual(duplicate_decision, SyncWatermarkDecision("duplicate", "same_source_sequence_same_payload"))

        conflict_watermark = SimpleNamespace(last_source_sequence=10, last_payload_hash="0" * 64)
        conflict_decision = await _evaluate_sync_watermark(
            FakeDB([ScalarFirstResult(None), ScalarFirstResult(conflict_watermark)]),
            context,
        )
        self.assertEqual(conflict_decision, SyncWatermarkDecision("conflict", "same_source_sequence_different_payload"))

    async def test_stale_event_is_processed_without_applying_row(self):
        db = FakeDB()
        item = sync_item(9)

        with patch(
            "api.routers.sync._evaluate_sync_watermark",
            new=AsyncMock(return_value=SyncWatermarkDecision("stale", "older_source_sequence")),
        ), patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")) as apply_item:
            result = await receive_sync_data(items=[item], request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "success", "processed": 1})
        apply_item.assert_not_awaited()

    async def test_equal_sequence_conflict_is_reported_without_applying_row(self):
        db = FakeDB()
        item = sync_item(10)

        with patch(
            "api.routers.sync._evaluate_sync_watermark",
            new=AsyncMock(return_value=SyncWatermarkDecision("conflict", "same_source_sequence_different_payload")),
        ), patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")) as apply_item:
            result = await receive_sync_data(items=[item], request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["error_items"][0]["reason"], "same_source_sequence_different_payload")
        apply_item.assert_not_awaited()

    async def test_iran_authoritative_table_rejects_foreign_source(self):
        db = FakeDB()
        data = {"key": "offer_expiry_minutes", "value": "20"}
        item = {
            "table": "trading_settings",
            "operation": "UPDATE",
            "id": 0,
            "data": data,
            "change_log_id": 30,
            "sync_meta": build_sync_metadata(
                "trading_settings",
                0,
                "UPDATE",
                data,
                change_log_id=30,
                source_server="foreign",
            ),
        }

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")) as apply_item:
            result = await receive_sync_data(items=[item], request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["error_items"][0]["reason"], "source_authority_forbidden:foreign")
        apply_item.assert_not_awaited()

    async def test_market_runtime_state_rejects_foreign_source_under_iran_authority(self):
        db = FakeDB()
        data = {
            "id": 1,
            "is_open": False,
            "active_web_notice_visible": True,
            "offers_since_last_open": 0,
            "last_transition_at": "2026-06-28T15:30:00+00:00",
        }
        item = {
            "table": "market_runtime_state",
            "operation": "UPDATE",
            "id": 1,
            "data": data,
            "change_log_id": 31,
            "sync_meta": build_sync_metadata(
                "market_runtime_state",
                1,
                "UPDATE",
                data,
                change_log_id=31,
                source_server="foreign",
            ),
        }

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")) as apply_item:
            result = await receive_sync_data(items=[item], request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["error_items"][0]["reason"], "source_authority_forbidden:foreign")
        apply_item.assert_not_awaited()

    async def test_deferred_item_records_watermark_only_after_retry_apply(self):
        db = FakeDB()
        item = sync_item(11)

        with patch(
            "api.routers.sync._evaluate_sync_watermark",
            new=AsyncMock(return_value=SyncWatermarkDecision("apply")),
        ) as evaluate, patch(
            "api.routers.sync._apply_item",
            new=AsyncMock(side_effect=["deferred", "ok"]),
        ), patch(
            "api.routers.sync._record_sync_watermark_applied",
            new=AsyncMock(),
        ) as record_watermark:
            result = await receive_sync_data(items=[item], request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "success", "processed": 1})
        self.assertEqual(evaluate.await_count, 2)
        record_watermark.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
