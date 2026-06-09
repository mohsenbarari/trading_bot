import builtins
import hashlib
import hmac
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import IntegrityError

from api.routers.sync import _apply_item, _build_upsert_stmt, receive_sync_data, resync_from_changelog


class AsyncNullContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class ApplyDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.execute_calls = []

    def begin_nested(self):
        return AsyncNullContext()

    async def execute(self, stmt, execution_options=None):
        self.execute_calls.append((stmt, execution_options))
        if self.execute_results:
            next_result = self.execute_results.pop(0)
            if isinstance(next_result, Exception):
                raise next_result
            return next_result
        return SimpleNamespace()


class ReceiveDB:
    def __init__(self, execute_results=None, fail_commit_on=None):
        self.execute_results = list(execute_results or [])
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit_on = fail_commit_on

    async def execute(self, stmt, *args, **kwargs):
        text = str(stmt)
        if "setval(" in text:
            return SimpleNamespace()
        if self.execute_results:
            next_result = self.execute_results.pop(0)
            if isinstance(next_result, Exception):
                raise next_result
            return next_result
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))

    async def commit(self):
        self.commits += 1
        if self.fail_commit_on and self.commits == self.fail_commit_on:
            raise RuntimeError("publish commit failed")

    async def rollback(self):
        self.rollbacks += 1

    def begin_nested(self):
        return AsyncNullContext()


class FakeInsertBuilder:
    def __init__(self):
        self.values_payload = None
        self.excluded = {
            "trades_count": "excluded-trades",
            "full_name": "excluded-name",
            "price": "excluded-price",
        }
        self.conflict_payload = None

    def values(self, **kwargs):
        self.values_payload = kwargs
        return self

    def on_conflict_do_update(self, index_elements, set_):
        self.conflict_payload = (index_elements, set_)
        return self.conflict_payload


class UpdateBuilder:
    def where(self, _clause):
        return self

    def values(self, **kwargs):
        return "MERGE_UPDATE"


class DeleteBuilder:
    def where(self, _clause):
        return "DELETE_STMT"


class FakeOfferExecuteResult:
    def __init__(self, offer):
        self._offer = offer

    def scalars(self):
        return SimpleNamespace(first=lambda: self._offer)


class FakeSelect:
    def options(self, *args, **kwargs):
        return self

    def where(self, *args, **kwargs):
        return self

    def with_for_update(self, **kwargs):
        return self

    def __str__(self):
        return "SELECT offer"


class FakeExecuteResult:
    def __init__(self, values):
        self._values = list(values)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))


class FakeAsyncClient:
    def __init__(self, response=None, calls=None, **kwargs):
        self.response = response
        self.calls = calls if calls is not None else []
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, content, headers):
        self.calls.append((url, content, headers))
        return self.response


def make_entry(entry_id, **overrides):
    data = {
        "id": entry_id,
        "operation": "INSERT",
        "table_name": "users",
        "record_id": entry_id,
        "data": {"full_name": f"User {entry_id}"},
        "hash": f"hash-{entry_id}",
        "timestamp": datetime(2026, 1, 1, 12, 0, 0),
        "synced": False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class SyncRouterRemainingPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_upsert_stmt_covers_user_and_default_tables(self):
        builder = FakeInsertBuilder()
        user_model = type("UserModel", (), {"trades_count": "current-trades", "full_name": "current-name"})

        with patch("api.routers.sync.pg_insert", return_value=builder), patch(
            "api.routers.sync.sa_func.greatest",
            side_effect=lambda left, right: ("greatest", left, right),
        ):
            result = _build_upsert_stmt(user_model, "users", {"trades_count": 8, "full_name": "Ali"})

        self.assertEqual(result[0], ["id"])
        self.assertEqual(result[1]["trades_count"], ("greatest", "current-trades", "excluded-trades"))
        self.assertEqual(result[1]["full_name"], "excluded-name")

        other_builder = FakeInsertBuilder()
        with patch("api.routers.sync.pg_insert", return_value=other_builder):
            result = _build_upsert_stmt(object, "offers", {"price": 100})

        self.assertEqual(result, (["id"], {"price": 100}))

    async def test_apply_item_covers_merge_failure_generic_integrity_and_unknown_operation(self):
        duplicate_error = IntegrityError("stmt", {}, Exception("duplicate key value violates unique constraint"))
        db = ApplyDB([duplicate_error, RuntimeError("merge failed")])
        model = type("DummyUserModel", (), {"telegram_id": object()})

        with patch("api.routers.sync._build_upsert_stmt", return_value="UPSERT"), patch(
            "api.routers.sync.update", return_value=UpdateBuilder()
        ), patch("api.routers.sync.logger") as logger_mock:
            result = await _apply_item(
                db,
                "users",
                "INSERT",
                5,
                {"telegram_id": 12345, "full_name": "User Name"},
                model=model,
                new_offers=[],
            )
        self.assertEqual(result, "error")
        rendered_merge_log = repr(logger_mock.error.call_args_list[0])
        self.assertNotIn("12345", rendered_merge_log)
        self.assertIn("natural_value_hash", rendered_merge_log)

        generic_error = IntegrityError("stmt", {}, Exception("check constraint failed"))
        db = ApplyDB([generic_error])
        with patch("api.routers.sync._build_upsert_stmt", return_value="UPSERT"):
            result = await _apply_item(
                db,
                "users",
                "INSERT",
                6,
                {"telegram_id": 9},
                model=model,
                new_offers=[],
            )
        self.assertEqual(result, "error")

        result = await _apply_item(ApplyDB(), "users", "UNKNOWN", 1, {}, model=object, new_offers=[])
        self.assertEqual(result, "error")

    async def test_receive_sync_data_covers_notification_failure_unknown_table_and_apply_exception(self):
        db = ReceiveDB()
        items = [
            {"type": "notification", "chat_id": 123, "text": "hi"},
            {"table": "mystery", "operation": "INSERT", "id": 8, "data": {}},
        ]

        with patch("core.notifications.send_telegram_message", new=AsyncMock(side_effect=RuntimeError("telegram down"))), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "success", "processed": 0})

        db = ReceiveDB()
        items = [{"table": "users", "operation": "INSERT", "id": 1, "data": {"telegram_id": 10}}]
        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=RuntimeError("apply boom"))), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "partial", "processed": 0, "errors": 1})

    async def test_receive_sync_data_covers_deferred_retry_error_and_exception(self):
        items = [{"table": "users", "operation": "INSERT", "id": 1, "data": {"telegram_id": 10}}]

        db = ReceiveDB()
        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=["deferred", "error"])), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)
        self.assertEqual(result, {"status": "partial", "processed": 0, "errors": 1})

        db = ReceiveDB()
        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=["deferred", RuntimeError("retry boom")])), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)
        self.assertEqual(result, {"status": "partial", "processed": 0, "errors": 1})

    async def test_receive_sync_data_tolerates_cache_invalidation_failures(self):
        db = ReceiveDB()
        items = [{"table": "commodities", "operation": "INSERT", "id": 3, "data": {"name": "gold"}}]

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ), patch("core.cache.invalidate_commodities_cache", new=AsyncMock(side_effect=RuntimeError("cache fail"))), patch(
            "bot.utils.redis_helpers.invalidate_commodity_cache", new=AsyncMock(side_effect=RuntimeError("bot fail"))
        ):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "success", "processed": 1})

    async def test_receive_sync_data_covers_offer_publish_import_and_runtime_failures(self):
        items = [{"table": "offers", "operation": "INSERT", "id": 7, "data": {"price": 11}}]

        async def fake_apply_item(db_arg, table, operation, record_id, data, model, new_offers):
            new_offers.append(record_id)
            return "ok"

        original_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "api.routers.offers":
                raise ImportError("no offers")
            return original_import(name, globals, locals, fromlist, level)

        db = ReceiveDB()
        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("builtins.__import__", side_effect=fake_import):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)
        self.assertEqual(result, {"status": "success", "processed": 1})

        offer = SimpleNamespace(id=7, channel_message_id=None, user=SimpleNamespace(id=1), commodity=SimpleNamespace(id=2))
        db = ReceiveDB([FakeOfferExecuteResult(offer)])
        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.select", return_value=FakeSelect()), patch(
            "sqlalchemy.orm.selectinload", side_effect=lambda *args, **kwargs: object()
        ), patch("api.routers.offers.send_offer_to_channel", new=AsyncMock(side_effect=RuntimeError("publish boom"))):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)
        self.assertEqual(result, {"status": "success", "processed": 1})

        offer = SimpleNamespace(id=7, channel_message_id=None, user=SimpleNamespace(id=1), commodity=SimpleNamespace(id=2))
        db = ReceiveDB([FakeOfferExecuteResult(offer)], fail_commit_on=3)
        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.select", return_value=FakeSelect()), patch(
            "sqlalchemy.orm.selectinload", side_effect=lambda *args, **kwargs: object()
        ), patch("api.routers.offers.send_offer_to_channel", new=AsyncMock(return_value=555)):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)
        self.assertEqual(result, {"status": "success", "processed": 1})

    async def test_resync_covers_table_filter_all_invalid_batches_and_non_200_response(self):
        request = SimpleNamespace(headers={"X-Dev-Api-Key": "dev-key"})
        db = ReceiveDB([FakeExecuteResult([])])
        with patch("api.routers.sync.settings.dev_api_key", "dev-key"), patch(
            "api.routers.sync.default_peer_server_url", return_value="https://peer.example"
        ), patch("api.routers.sync.settings.sync_api_key", "secret"):
            result = await resync_from_changelog(request=request, db=db, table_filter="users")
        self.assertEqual(result, {"status": "ok", "message": "No unsynced entries found", "processed": 0})

        bad = make_entry(2, data="not-json")
        db = ReceiveDB([FakeExecuteResult([bad])])
        calls = []
        client_factory = lambda **kwargs: FakeAsyncClient(
            response=SimpleNamespace(status_code=200, text="ok"),
            calls=calls,
            **kwargs,
        )
        with patch("api.routers.sync.settings.dev_api_key", "dev-key"), patch(
            "api.routers.sync.default_peer_server_url", return_value="https://peer.example"
        ), patch("api.routers.sync.settings.sync_api_key", "secret"), patch(
            "httpx.AsyncClient", side_effect=client_factory
        ):
            result = await resync_from_changelog(request=request, db=db)
        self.assertEqual(result, {"status": "ok", "processed": 0, "errors": 1, "total_entries": 1})
        self.assertEqual(calls, [])

        entry = make_entry(3)
        db = ReceiveDB([FakeExecuteResult([entry])])
        calls = []
        client_factory = lambda **kwargs: FakeAsyncClient(
            response=SimpleNamespace(status_code=503, text="bad gateway"),
            calls=calls,
            **kwargs,
        )
        with patch("api.routers.sync.settings.dev_api_key", "dev-key"), patch(
            "api.routers.sync.default_peer_server_url", return_value="https://peer.example"
        ), patch("api.routers.sync.settings.sync_api_key", "secret"), patch(
            "api.routers.sync.time.time", return_value=1_700_000_111
        ), patch("httpx.AsyncClient", side_effect=client_factory), patch("api.routers.sync.logger") as logger_mock:
            result = await resync_from_changelog(request=request, db=db)
        self.assertEqual(result, {"status": "ok", "processed": 0, "errors": 1, "total_entries": 1})
        self.assertFalse(entry.synced)
        self.assertEqual(len(calls), 1)
        rendered_log_call = repr(logger_mock.warning.call_args)
        self.assertNotIn("bad gateway", rendered_log_call)
        self.assertIn("peer_response_sha256", rendered_log_call)


if __name__ == "__main__":
    unittest.main()
