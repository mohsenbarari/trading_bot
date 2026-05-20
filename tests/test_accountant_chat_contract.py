import unittest
from types import SimpleNamespace

from core.services.accountant_chat_contract import (
    AccountantChatIdentity,
    apply_accountant_identity_to_direct_conversation_row,
    apply_accountant_identity_to_message_payload,
    build_relation_aware_display_name,
    collect_message_identity_user_ids,
    load_accountant_chat_identity_map,
    resolve_direct_sender_display_name,
)
from models.accountant_relation import AccountantRelationStatus


class FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class FakeExecuteResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return FakeScalarResult(self._values)


class FakeDB:
    def __init__(self, values):
        self.values = values
        self.execute_calls = 0

    async def execute(self, _stmt):
        self.execute_calls += 1
        return FakeExecuteResult(self.values)


class AccountantChatContractTests(unittest.IsolatedAsyncioTestCase):
    def test_build_relation_aware_display_name_prefers_relation_and_falls_back(self):
        self.assertEqual(build_relation_aware_display_name("  دفتر علی ", "owner-a"), "دفتر علی")
        self.assertEqual(build_relation_aware_display_name("   ", "owner-a"), "owner-a")
        self.assertEqual(build_relation_aware_display_name(None, " owner-b "), "owner-b")

    async def test_load_accountant_chat_identity_map_handles_empty_and_skips_invalid_relations(self):
        empty_db = FakeDB(values=[])
        self.assertEqual(await load_accountant_chat_identity_map(empty_db, []), {})
        self.assertEqual(empty_db.execute_calls, 0)

        owner = SimpleNamespace(id=201, account_name="owner-201", avatar_file_id="avatar-201", is_deleted=False)
        valid_relation = SimpleNamespace(
            accountant_user_id=101,
            relation_display_name="دفتر مالک",
            owner_user=owner,
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
        )
        missing_owner_relation = SimpleNamespace(
            accountant_user_id=102,
            relation_display_name="دفتر گمشده",
            owner_user=None,
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
        )
        deleted_owner_relation = SimpleNamespace(
            accountant_user_id=103,
            relation_display_name="دفتر حذف‌شده",
            owner_user=SimpleNamespace(id=202, account_name="owner-202", avatar_file_id=None, is_deleted=True),
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
        )
        db = FakeDB(values=[valid_relation, missing_owner_relation, deleted_owner_relation])

        identity_map = await load_accountant_chat_identity_map(db, [101, 102, 103])

        self.assertEqual(db.execute_calls, 1)
        self.assertEqual(sorted(identity_map.keys()), [101])
        self.assertEqual(identity_map[101].display_name, "دفتر مالک")
        self.assertEqual(identity_map[101].profile_user_id, 201)
        self.assertEqual(identity_map[101].profile_account_name, "owner-201")
        self.assertEqual(identity_map[101].profile_avatar_file_id, "avatar-201")
        self.assertEqual(identity_map[101].resolved_from_accountant_id, 101)
        self.assertEqual(identity_map[101].highlight_accountant_user_id, 101)
        self.assertEqual(identity_map[101].highlight_accountant_relation_display_name, "دفتر مالک")

    async def test_resolve_direct_sender_display_name_uses_identity_map_and_falls_back(self):
        owner = SimpleNamespace(id=201, account_name="owner-201", avatar_file_id="avatar-201", is_deleted=False)
        relation = SimpleNamespace(
            accountant_user_id=101,
            relation_display_name="دفتر مالک",
            owner_user=owner,
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
        )
        db = FakeDB(values=[relation])

        resolved = await resolve_direct_sender_display_name(
            db,
            user=SimpleNamespace(id=101, account_name="raw-accountant"),
        )
        fallback = await resolve_direct_sender_display_name(
            FakeDB(values=[]),
            user=SimpleNamespace(id=9, account_name="raw-user"),
        )
        no_db_fallback = await resolve_direct_sender_display_name(
            object(),
            user=SimpleNamespace(id=7, account_name="raw-no-db"),
        )

        self.assertEqual(resolved, "دفتر مالک")
        self.assertEqual(fallback, "raw-user")
        self.assertEqual(no_db_fallback, "raw-no-db")

    def test_apply_accountant_identity_to_direct_conversation_row_sets_profile_fields(self):
        identity = AccountantChatIdentity(
            raw_user_id=101,
            display_name="دفتر مالک",
            profile_user_id=201,
            profile_account_name="owner-201",
            profile_avatar_file_id="avatar-201",
            resolved_from_accountant_id=101,
            highlight_accountant_user_id=101,
            highlight_accountant_relation_display_name="دفتر مالک",
        )

        enriched = apply_accountant_identity_to_direct_conversation_row(
            {"other_user_id": 101, "other_user_name": "raw-accountant", "avatar_file_id": None},
            {101: identity},
        )
        self.assertEqual(enriched["other_user_name"], "دفتر مالک")
        self.assertEqual(enriched["avatar_file_id"], "avatar-201")
        self.assertEqual(enriched["profile_user_id"], 201)
        self.assertEqual(enriched["profile_account_name"], "owner-201")
        self.assertEqual(enriched["resolved_from_accountant_id"], 101)

        fallback = apply_accountant_identity_to_direct_conversation_row(
            {"other_user_id": 9, "other_user_name": "raw-user"},
            {},
        )
        self.assertEqual(fallback["profile_user_id"], 9)
        self.assertEqual(fallback["profile_account_name"], "raw-user")

        unchanged = apply_accountant_identity_to_direct_conversation_row(
            {"other_user_id": 0, "other_user_name": "ignored"},
            {101: identity},
        )
        self.assertNotIn("profile_user_id", unchanged)

    def test_collect_message_identity_user_ids_and_apply_message_payload(self):
        user_ids = collect_message_identity_user_ids(
            [
                {"sender_id": 101, "forwarded_from_id": 202},
                SimpleNamespace(sender_id="303", forwarded_from_id=None),
                {"sender_id": "bad", "forwarded_from_id": -1},
            ]
        )
        self.assertEqual(user_ids, {101, 202, 303})

        sender_identity = AccountantChatIdentity(
            raw_user_id=101,
            display_name="دفتر فرستنده",
            profile_user_id=501,
            profile_account_name="owner-501",
            resolved_from_accountant_id=101,
            highlight_accountant_user_id=101,
            highlight_accountant_relation_display_name="دفتر فرستنده",
        )
        forwarded_identity = AccountantChatIdentity(
            raw_user_id=202,
            display_name="دفتر هدایت",
            profile_user_id=502,
            profile_account_name="owner-502",
            resolved_from_accountant_id=202,
            highlight_accountant_user_id=202,
            highlight_accountant_relation_display_name="دفتر هدایت",
        )

        enriched = apply_accountant_identity_to_message_payload(
            {
                "sender_id": 101,
                "sender_name": "raw-sender",
                "forwarded_from_id": 202,
                "forwarded_from_name": "raw-forwarded",
            },
            {101: sender_identity, 202: forwarded_identity},
        )
        self.assertEqual(enriched["sender_name"], "دفتر فرستنده")
        self.assertEqual(enriched["sender_profile_user_id"], 501)
        self.assertEqual(enriched["sender_profile_account_name"], "owner-501")
        self.assertEqual(enriched["forwarded_from_name"], "دفتر هدایت")
        self.assertEqual(enriched["forwarded_from_profile_user_id"], 502)
        self.assertEqual(enriched["forwarded_from_profile_account_name"], "owner-502")

        fallback = apply_accountant_identity_to_message_payload(
            {
                "sender_id": 9,
                "sender_name": "raw-user",
                "forwarded_from_id": None,
                "forwarded_from_name": None,
            },
            {},
        )
        self.assertEqual(fallback["sender_profile_user_id"], 9)
        self.assertEqual(fallback["sender_profile_account_name"], "raw-user")
        self.assertNotIn("forwarded_from_profile_user_id", fallback)


if __name__ == "__main__":
    unittest.main()