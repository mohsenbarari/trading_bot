"""Characterization tests for api.routers.auth.update_my_avatar.

Covers ownership-resolution rejection, observable execution order on success,
and commit-failure behavior under P1-00 network isolation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException

from api.routers.auth import (
    _load_current_user_relation_context,
    _serialize_current_user_response,
    update_my_avatar,
)
from core.enums import UserAccountStatus, UserRole
from core.services.bot_access_policy import BotAccessDecision
from models.chat_file import ChatFile
from models.customer_relation import CustomerTier
import schemas


class InstrumentedUser:
    """Runtime-shaped user fake instrumented to record avatar assignment and serialization."""

    def __init__(self, order_tracker: list[str] | None = None, **overrides):
        self._order_tracker = order_tracker
        self._serialization_recorded = False

        data = {
            "id": 7,
            "telegram_id": None,
            "username": None,
            "full_name": "علی رضایی",
            "account_name": "ali",
            "mobile_number": "09120000000",
            "role": UserRole.STANDARD,
            "account_status": UserAccountStatus.ACTIVE,
            "deactivated_at": None,
            "messenger_grace_expires_at": None,
            "messenger_blocked_at": None,
            "has_bot_access": True,
            "is_deleted": False,
            "avatar_file_id": None,
            "created_at": datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc),
            "trading_restricted_until": None,
            "max_daily_trades": None,
            "max_active_commodities": None,
            "max_daily_requests": None,
            "limitations_expire_at": None,
            "trades_count": 0,
            "commodities_traded_count": 0,
            "channel_messages_count": 0,
            "last_seen_at": None,
            "can_block_users": True,
            "max_blocked_users": 10,
            "max_sessions": 1,
            "max_accountants": 3,
            "max_customers": 5,
            "offer_overtime_minutes": 0,
        }
        data.update(overrides)
        self._avatar_file_id = data.pop("avatar_file_id")
        self._full_name = data.pop("full_name")
        self.__dict__.update(data)

    @property
    def avatar_file_id(self) -> str | None:
        return self._avatar_file_id

    @avatar_file_id.setter
    def avatar_file_id(self, value: str | None) -> None:
        if self._order_tracker is not None:
            self._order_tracker.append("avatar_assignment")
        self._avatar_file_id = value

    @property
    def full_name(self) -> str:
        if self._order_tracker is not None and not self._serialization_recorded:
            self._serialization_recorded = True
            self._order_tracker.append("serialization")
        return self._full_name

    @full_name.setter
    def full_name(self, value: str) -> None:
        self._full_name = value


class FakeDb:
    """Runtime-shaped database fake with observable method execution."""

    def __init__(self, order_tracker: list[str] | None = None):
        self.order_tracker = order_tracker
        self.get = AsyncMock(side_effect=self._fake_get)
        self.commit = AsyncMock(side_effect=self._fake_commit)
        self.refresh = AsyncMock(side_effect=self._fake_refresh)
        self.rollback = AsyncMock()
        self._get_record = None

    def set_get_record(self, record):
        self._get_record = record

    async def _fake_get(self, model, ident):
        if self.order_tracker is not None:
            self.order_tracker.append("avatar_lookup")
        return self._get_record

    async def _fake_commit(self):
        if self.order_tracker is not None:
            self.order_tracker.append("commit")

    async def _fake_refresh(self, instance):
        if self.order_tracker is not None:
            self.order_tracker.append("refresh")


class UpdateMyAvatarCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_my_avatar_rejects_unowned_avatar(self):
        order_tracker: list[str] = []
        user = InstrumentedUser(
            order_tracker=order_tracker,
            id=7,
            avatar_file_id="initial-avatar-uuid",
        )
        payload = schemas.UserAvatarUpdate(avatar_file_id="foreign-avatar-uuid-999")
        fake_chat_file = SimpleNamespace(
            id="foreign-avatar-uuid-999",
            uploader_id=999,
            mime_type="image/png",
        )
        fake_db = FakeDb(order_tracker=order_tracker)
        fake_db.set_get_record(fake_chat_file)

        with patch(
            "api.routers.auth.is_user_accountant",
            new=AsyncMock(),
        ) as accountant_mock, patch(
            "api.routers.auth.get_active_customer_relation_for_customer",
            new=AsyncMock(),
        ) as customer_relation_mock, patch(
            "api.routers.auth.evaluate_bot_access",
            new=AsyncMock(),
        ) as bot_access_mock, patch(
            "api.routers.auth._load_current_user_relation_context",
            wraps=_load_current_user_relation_context,
        ) as load_context_spy, patch(
            "api.routers.auth._serialize_current_user_response",
            wraps=_serialize_current_user_response,
        ) as serialize_spy:
            with self.assertRaises(HTTPException) as ctx:
                await update_my_avatar(
                    payload=payload,
                    current_user=user,
                    db=fake_db,
                )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail,
            "فقط می\u200cتوانید از تصویر آپلودشده توسط خودتان استفاده کنید",
        )
        fake_db.get.assert_awaited_once_with(ChatFile, "foreign-avatar-uuid-999")
        self.assertIs(fake_db.get.call_args[0][0], ChatFile)
        self.assertEqual(fake_db.get.call_args[0][1], "foreign-avatar-uuid-999")

        self.assertEqual(user.avatar_file_id, "initial-avatar-uuid")
        self.assertEqual(order_tracker, ["avatar_lookup"])

        fake_db.commit.assert_not_called()
        fake_db.refresh.assert_not_called()
        fake_db.rollback.assert_not_called()
        load_context_spy.assert_not_called()
        serialize_spy.assert_not_called()
        accountant_mock.assert_not_called()
        customer_relation_mock.assert_not_called()
        bot_access_mock.assert_not_called()
        self.assertFalse(user._serialization_recorded)

    async def test_update_my_avatar_success_preserves_strict_observable_order_and_relations(self):
        order_tracker: list[str] = []
        user = InstrumentedUser(
            order_tracker=order_tracker,
            id=7,
            telegram_id=987654321,
            avatar_file_id="old-avatar-uuid",
        )
        payload = schemas.UserAvatarUpdate(avatar_file_id="new-avatar-uuid-42")
        fake_chat_file = SimpleNamespace(
            id="new-avatar-uuid-42",
            uploader_id=7,
            mime_type="image/png",
        )
        fake_db = FakeDb(order_tracker=order_tracker)
        fake_db.set_get_record(fake_chat_file)

        fake_customer_relation = SimpleNamespace(
            customer_tier=CustomerTier.TIER_1,
            owner_user_id=101,
            management_name="حسابداری شعبه ۱",
        )

        async def _mock_is_accountant(db_arg, user_id_arg):
            order_tracker.append("relation_queries:is_user_accountant")
            return False

        async def _mock_get_customer_relation(db_arg, user_id_arg):
            order_tracker.append("relation_queries:get_active_customer_relation_for_customer")
            return fake_customer_relation

        async def _mock_evaluate_bot_access(db_arg, user_arg):
            order_tracker.append("relation_queries:evaluate_bot_access")
            return BotAccessDecision(allowed=True)

        with patch(
            "api.routers.auth.is_user_accountant",
            new=AsyncMock(side_effect=_mock_is_accountant),
        ) as accountant_mock, patch(
            "api.routers.auth.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=_mock_get_customer_relation),
        ) as customer_relation_mock, patch(
            "api.routers.auth.evaluate_bot_access",
            new=AsyncMock(side_effect=_mock_evaluate_bot_access),
        ) as bot_access_mock:
            result = await update_my_avatar(
                payload=payload,
                current_user=user,
                db=fake_db,
            )

        expected_order = [
            "avatar_lookup",
            "avatar_assignment",
            "commit",
            "refresh",
            "relation_queries:is_user_accountant",
            "relation_queries:get_active_customer_relation_for_customer",
            "relation_queries:evaluate_bot_access",
            "serialization",
        ]
        self.assertEqual(order_tracker, expected_order)

        lookup_idx = order_tracker.index("avatar_lookup")
        assign_idx = order_tracker.index("avatar_assignment")
        commit_idx = order_tracker.index("commit")
        refresh_idx = order_tracker.index("refresh")
        rel_accountant_idx = order_tracker.index("relation_queries:is_user_accountant")
        rel_customer_idx = order_tracker.index("relation_queries:get_active_customer_relation_for_customer")
        rel_bot_idx = order_tracker.index("relation_queries:evaluate_bot_access")
        serialize_idx = order_tracker.index("serialization")

        self.assertLess(lookup_idx, assign_idx)
        self.assertLess(assign_idx, commit_idx)
        self.assertLess(commit_idx, refresh_idx)
        self.assertLess(refresh_idx, rel_accountant_idx)
        self.assertLess(rel_accountant_idx, rel_customer_idx)
        self.assertLess(rel_customer_idx, rel_bot_idx)
        self.assertLess(rel_bot_idx, serialize_idx)

        fake_db.get.assert_awaited_once_with(ChatFile, "new-avatar-uuid-42")
        fake_db.commit.assert_awaited_once_with()
        self.assertEqual(fake_db.commit.call_args, unittest.mock.call())
        fake_db.refresh.assert_awaited_once_with(user)
        self.assertIs(fake_db.refresh.call_args[0][0], user)

        accountant_mock.assert_awaited_once_with(fake_db, 7)
        customer_relation_mock.assert_awaited_once_with(fake_db, 7)
        bot_access_mock.assert_awaited_once_with(fake_db, user)

        self.assertIsInstance(result, schemas.UserRead)
        self.assertEqual(result.avatar_file_id, "new-avatar-uuid-42")
        self.assertEqual(user.avatar_file_id, "new-avatar-uuid-42")
        self.assertFalse(result.is_accountant)
        self.assertTrue(result.is_customer)
        self.assertEqual(result.customer_tier, CustomerTier.TIER_1)
        self.assertEqual(result.customer_owner_user_id, 101)
        self.assertEqual(result.customer_management_name, "حسابداری شعبه ۱")
        self.assertTrue(result.telegram_linked)
        self.assertFalse(result.can_connect_telegram)
        self.assertIsNone(result.telegram_link_denial_reason)

    async def test_update_my_avatar_commit_failure_preserves_sentinel_and_prevents_subsequent_phases(self):
        class SentinelCommitError(Exception):
            pass

        sentinel_error = SentinelCommitError("database transaction failed during avatar commit")

        order_tracker: list[str] = []
        user = InstrumentedUser(
            order_tracker=order_tracker,
            id=7,
            avatar_file_id="initial-avatar-uuid",
        )
        payload = schemas.UserAvatarUpdate(avatar_file_id="valid-avatar-uuid-99")
        fake_chat_file = SimpleNamespace(
            id="valid-avatar-uuid-99",
            uploader_id=7,
            mime_type="image/jpeg",
        )
        fake_db = FakeDb(order_tracker=order_tracker)
        fake_db.set_get_record(fake_chat_file)

        async def _failing_commit():
            order_tracker.append("commit")
            raise sentinel_error

        fake_db.commit.side_effect = _failing_commit

        with patch(
            "api.routers.auth.is_user_accountant",
            new=AsyncMock(),
        ) as accountant_mock, patch(
            "api.routers.auth.get_active_customer_relation_for_customer",
            new=AsyncMock(),
        ) as customer_relation_mock, patch(
            "api.routers.auth.evaluate_bot_access",
            new=AsyncMock(),
        ) as bot_access_mock, patch(
            "api.routers.auth._load_current_user_relation_context",
            wraps=_load_current_user_relation_context,
        ) as load_context_spy, patch(
            "api.routers.auth._serialize_current_user_response",
            wraps=_serialize_current_user_response,
        ) as serialize_spy:
            with self.assertRaises(SentinelCommitError) as ctx:
                await update_my_avatar(
                    payload=payload,
                    current_user=user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, sentinel_error)
        self.assertEqual(user.avatar_file_id, "valid-avatar-uuid-99")

        expected_order = ["avatar_lookup", "avatar_assignment", "commit"]
        self.assertEqual(order_tracker, expected_order)
        self.assertLess(
            order_tracker.index("avatar_lookup"),
            order_tracker.index("avatar_assignment"),
        )
        self.assertLess(
            order_tracker.index("avatar_assignment"),
            order_tracker.index("commit"),
        )

        fake_db.get.assert_awaited_once_with(ChatFile, "valid-avatar-uuid-99")
        fake_db.commit.assert_awaited_once_with()
        fake_db.refresh.assert_not_called()
        fake_db.rollback.assert_not_called()
        load_context_spy.assert_not_called()
        serialize_spy.assert_not_called()
        accountant_mock.assert_not_called()
        customer_relation_mock.assert_not_called()
        bot_access_mock.assert_not_called()
        self.assertFalse(user._serialization_recorded)


if __name__ == "__main__":
    unittest.main()
