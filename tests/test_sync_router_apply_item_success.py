import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.routers.sync import _apply_item


class AsyncNullContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDB:
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


class ScalarOneOrNoneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeInsertBuilder:
    def __init__(self):
        self.values_payload = None
        self.conflict_payload = None

    def values(self, **kwargs):
        self.values_payload = kwargs
        return self

    def on_conflict_do_update(self, index_elements, set_):
        self.conflict_payload = (index_elements, set_)
        return "TRADING_UPSERT"


class UpdateBuilder:
    def __init__(self):
        self.where_clause = None
        self.values_payload = None

    def where(self, clause):
        self.where_clause = clause
        return self

    def values(self, **kwargs):
        self.values_payload = kwargs
        return "MERGE_UPDATE"


class SyncRouterApplyItemSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_item_handles_trading_settings_and_offer_insert_success(self):
        insert_builder = FakeInsertBuilder()
        db = FakeDB()

        with patch("api.routers.sync.pg_insert", return_value=insert_builder):
            result = await _apply_item(
                db,
                "trading_settings",
                "INSERT",
                1,
                {"key": "offer_expiry_minutes", "value": "15"},
                model=object,
                new_offers=[],
            )
        self.assertEqual(result, "ok")
        self.assertEqual(insert_builder.values_payload, {"key": "offer_expiry_minutes", "value": "15"})
        self.assertEqual(insert_builder.conflict_payload, (["key"], {"key": "offer_expiry_minutes", "value": "15"}))
        self.assertEqual(db.execute_calls[0], ("TRADING_UPSERT", {"is_sync": True}))

        new_offers = []
        offer_data = {"price": 12, "channel_message_id": 99}
        db = FakeDB()
        with patch("api.routers.sync._build_upsert_stmt", return_value="UPSERT") as builder, patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ):
            result = await _apply_item(
                db,
                "offers",
                "INSERT",
                8,
                offer_data,
                model=object,
                new_offers=new_offers,
            )
        self.assertEqual(result, "ok")
        self.assertEqual(new_offers, [8])
        self.assertNotIn("channel_message_id", offer_data)
        self.assertEqual(offer_data["id"], 8)
        builder.assert_called_once_with(object, "offers", offer_data)
        self.assertEqual(db.execute_calls[0], ("UPSERT", {"is_sync": True}))

    async def test_apply_item_merges_unique_violation_by_natural_key(self):
        duplicate_error = Exception("duplicate key value violates unique constraint")
        db = FakeDB([
            __import__("sqlalchemy").exc.IntegrityError("stmt", {}, duplicate_error),
            SimpleNamespace(),
        ])
        model = type("DummyUserModel", (), {"telegram_id": object()})
        update_builder = UpdateBuilder()

        with patch("api.routers.sync._build_upsert_stmt", return_value="UPSERT"), patch(
            "api.routers.sync.update", return_value=update_builder
        ):
            result = await _apply_item(
                db,
                "users",
                "INSERT",
                5,
                {"telegram_id": 12345, "full_name": "User Name"},
                model=model,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        self.assertEqual(update_builder.values_payload, {"full_name": "User Name"})
        self.assertEqual(db.execute_calls[1], ("MERGE_UPDATE", {"is_sync": True}))

    async def test_apply_item_merges_mandatory_chat_and_membership_to_local_singletons(self):
        chat_data = {
            "type": "channel",
            "title": "اطلاع‌رسانی",
            "description": "کانال اجباری اطلاع‌رسانی سامانه",
            "is_system": True,
            "is_mandatory": True,
        }
        db = FakeDB([ScalarOneOrNoneResult(44)])
        with patch("api.routers.sync._build_upsert_stmt", return_value="CHAT_UPSERT") as builder:
            result = await _apply_item(
                db,
                "chats",
                "INSERT",
                9,
                chat_data,
                model=object,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        self.assertEqual(chat_data["id"], 44)
        builder.assert_called_once_with(object, "chats", chat_data)
        self.assertEqual(db.execute_calls[-1], ("CHAT_UPSERT", {"is_sync": True}))

        member_data = {
            "chat_id": 9,
            "user_id": 5,
            "role": "member",
            "membership_status": "active",
            "chat_type": "channel",
            "chat_is_system": True,
            "chat_is_mandatory": True,
        }
        db = FakeDB([ScalarOneOrNoneResult(44), ScalarOneOrNoneResult(77)])
        with patch("api.routers.sync._build_upsert_stmt", return_value="MEMBER_UPSERT") as builder:
            result = await _apply_item(
                db,
                "chat_members",
                "INSERT",
                12,
                member_data,
                model=object,
                new_offers=[],
            )

        self.assertEqual(result, "ok")
        self.assertEqual(member_data["chat_id"], 44)
        self.assertEqual(member_data["id"], 77)
        self.assertNotIn("chat_type", member_data)
        self.assertNotIn("chat_is_system", member_data)
        self.assertNotIn("chat_is_mandatory", member_data)
        builder.assert_called_once_with(object, "chat_members", member_data)
        self.assertEqual(db.execute_calls[-1], ("MEMBER_UPSERT", {"is_sync": True}))


if __name__ == "__main__":
    unittest.main()