"""Characterization tests for api.routers.auth.update_my_offer_overtime service errors and commit failure phases.

Freezes observable behavior and phase suppression under P1-00 network isolation:
1. OfferOvertimePreferenceError maps to HTTP 400 with exact detail, cause preserved,
   pre-call user state unchanged, and commit/refresh/rollback/cache invalidation suppressed.
2. OfferOvertimePreferenceNotAllowedError maps to HTTP 403 with exact detail, cause preserved,
   pre-call user state unchanged, and commit/refresh/rollback/cache invalidation suppressed.
3. Commit failure preserves observable in-memory mutation, precedes single commit attempt,
   and suppresses refresh, rollback, cache invalidation, and response construction phases.
"""

from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException

from api.routers.auth import update_my_offer_overtime
from core.enums import UserAccountStatus, UserRole
from core.server_routing import SERVER_IRAN, override_current_server
from core.services.offer_overtime_preference_service import (
    INVALID_OVERTIME_VALUE_MESSAGE,
    OVERTIME_NOT_AVAILABLE_MESSAGE,
    OfferOvertimePreferenceError,
    OfferOvertimePreferenceNotAllowedError,
    OvertimePreferenceSaveResult,
)
import schemas


class InstrumentedUser:
    """Runtime-shaped user fake instrumented to record offer_overtime_minutes state."""

    def __init__(self, order_tracker: list[str] | None = None, **overrides):
        self._order_tracker = order_tracker
        self._tracking_active = True
        self._mutation_count = 0

        data = {
            "id": 7,
            "telegram_id": 998877,
            "username": "testuser",
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
        self._offer_overtime_minutes = data.pop("offer_overtime_minutes")
        self.__dict__.update(data)

    @property
    def offer_overtime_minutes(self) -> int:
        return self._offer_overtime_minutes

    @offer_overtime_minutes.setter
    def offer_overtime_minutes(self, value: int) -> None:
        if self._tracking_active:
            self._mutation_count += 1
        self._offer_overtime_minutes = value

    def stop_tracking(self) -> None:
        self._tracking_active = False

    @property
    def mutation_count(self) -> int:
        return self._mutation_count


class FakeDb:
    """Runtime-shaped database fake with observable method execution."""

    def __init__(self, order_tracker: list[str] | None = None):
        self.order_tracker = order_tracker
        self.commit = AsyncMock(side_effect=self._fake_commit)
        self.refresh = AsyncMock(side_effect=self._fake_refresh)
        self.rollback = AsyncMock(side_effect=self._fake_rollback)

    async def _fake_commit(self):
        if self.order_tracker is not None:
            self.order_tracker.append("commit")

    async def _fake_refresh(self, instance):
        if self.order_tracker is not None:
            self.order_tracker.append("refresh")

    async def _fake_rollback(self):
        if self.order_tracker is not None:
            self.order_tracker.append("rollback")


class UpdateMyOfferOvertimeFailuresCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_my_offer_overtime_service_error_maps_to_http_400_and_suppresses_phases(self):
        order_tracker: list[str] = []
        user = InstrumentedUser(order_tracker=order_tracker, id=7, telegram_id=998877, offer_overtime_minutes=0)
        fake_db = FakeDb(order_tracker=order_tracker)
        payload = schemas.UserOfferOvertimeUpdate(offer_overtime_minutes=5)
        self.assertEqual(payload.offer_overtime_minutes, 5)

        service_error = OfferOvertimePreferenceError(INVALID_OVERTIME_VALUE_MESSAGE)

        async def _failing_persist(db_arg, user_arg, value_arg):
            order_tracker.append("persist")
            raise service_error

        persist_mock = AsyncMock(side_effect=_failing_persist)
        cache_mock = AsyncMock()

        with override_current_server(SERVER_IRAN), patch(
            "core.services.offer_overtime_preference_service.persist_overtime_preference",
            new=persist_mock,
        ), patch(
            "core.cache.invalidate_user_cache",
            new=cache_mock,
        ):
            with self.assertRaises(HTTPException) as ctx:
                await update_my_offer_overtime(
                    payload=payload,
                    current_user=user,
                    db=fake_db,
                )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, INVALID_OVERTIME_VALUE_MESSAGE)
        self.assertEqual(ctx.exception.detail, service_error.message)
        self.assertIs(ctx.exception.__cause__, service_error)

        persist_mock.assert_awaited_once_with(fake_db, user, payload.offer_overtime_minutes)
        self.assertEqual(persist_mock.await_count, 1)
        self.assertIs(persist_mock.call_args[0][0], fake_db)
        self.assertIs(persist_mock.call_args[0][1], user)
        self.assertEqual(persist_mock.call_args[0][2], 5)

        self.assertEqual(user.offer_overtime_minutes, 0)
        self.assertEqual(user.mutation_count, 0)

        fake_db.commit.assert_not_called()
        self.assertEqual(fake_db.commit.await_count, 0)
        fake_db.refresh.assert_not_called()
        self.assertEqual(fake_db.refresh.await_count, 0)
        fake_db.rollback.assert_not_called()
        self.assertEqual(fake_db.rollback.await_count, 0)
        cache_mock.assert_not_called()
        self.assertEqual(cache_mock.await_count, 0)

        self.assertEqual(order_tracker, ["persist"])
        self.assertNotIn("commit", order_tracker)
        self.assertNotIn("refresh", order_tracker)
        self.assertNotIn("rollback", order_tracker)

    async def test_update_my_offer_overtime_not_allowed_maps_to_http_403_and_suppresses_phases(self):
        order_tracker: list[str] = []
        user = InstrumentedUser(order_tracker=order_tracker, id=7, telegram_id=998877, offer_overtime_minutes=0)
        fake_db = FakeDb(order_tracker=order_tracker)
        payload = schemas.UserOfferOvertimeUpdate(offer_overtime_minutes=3)
        self.assertEqual(payload.offer_overtime_minutes, 3)

        service_error = OfferOvertimePreferenceNotAllowedError(OVERTIME_NOT_AVAILABLE_MESSAGE)

        async def _failing_persist(db_arg, user_arg, value_arg):
            order_tracker.append("persist")
            raise service_error

        persist_mock = AsyncMock(side_effect=_failing_persist)
        cache_mock = AsyncMock()

        with override_current_server(SERVER_IRAN), patch(
            "core.services.offer_overtime_preference_service.persist_overtime_preference",
            new=persist_mock,
        ), patch(
            "core.cache.invalidate_user_cache",
            new=cache_mock,
        ):
            with self.assertRaises(HTTPException) as ctx:
                await update_my_offer_overtime(
                    payload=payload,
                    current_user=user,
                    db=fake_db,
                )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, OVERTIME_NOT_AVAILABLE_MESSAGE)
        self.assertEqual(ctx.exception.detail, service_error.message)
        self.assertIs(ctx.exception.__cause__, service_error)

        persist_mock.assert_awaited_once_with(fake_db, user, payload.offer_overtime_minutes)
        self.assertEqual(persist_mock.await_count, 1)
        self.assertIs(persist_mock.call_args[0][0], fake_db)
        self.assertIs(persist_mock.call_args[0][1], user)
        self.assertEqual(persist_mock.call_args[0][2], 3)

        self.assertEqual(user.offer_overtime_minutes, 0)
        self.assertEqual(user.mutation_count, 0)

        fake_db.commit.assert_not_called()
        self.assertEqual(fake_db.commit.await_count, 0)
        fake_db.refresh.assert_not_called()
        self.assertEqual(fake_db.refresh.await_count, 0)
        fake_db.rollback.assert_not_called()
        self.assertEqual(fake_db.rollback.await_count, 0)
        cache_mock.assert_not_called()
        self.assertEqual(cache_mock.await_count, 0)

        self.assertEqual(order_tracker, ["persist"])
        self.assertNotIn("commit", order_tracker)
        self.assertNotIn("refresh", order_tracker)
        self.assertNotIn("rollback", order_tracker)

    async def test_update_my_offer_overtime_commit_failure_preserves_mutation_and_suppresses_phases(self):
        class SentinelCommitError(Exception):
            """Unique sentinel exception during database commit."""

        sentinel_error = SentinelCommitError("database transaction failed during overtime preference commit")

        order_tracker: list[str] = []
        user = InstrumentedUser(order_tracker=order_tracker, id=7, telegram_id=998877, offer_overtime_minutes=0)
        self.assertIsNotNone(user.telegram_id)
        self.assertEqual(user.telegram_id, 998877)

        fake_db = FakeDb(order_tracker=order_tracker)

        async def _failing_commit():
            order_tracker.append("commit")
            raise sentinel_error

        fake_db.commit.side_effect = _failing_commit

        payload = schemas.UserOfferOvertimeUpdate(offer_overtime_minutes=5)
        self.assertEqual(payload.offer_overtime_minutes, 5)

        save_result = OvertimePreferenceSaveResult(
            offer_overtime_minutes=5,
            detail="مهلت پیشنهاد با موفقیت ثبت شد.",
            warning=None,
        )

        async def _succeeding_persist(db_arg, user_arg, value_arg):
            order_tracker.append("persist")
            user_arg.offer_overtime_minutes = value_arg
            return save_result

        persist_mock = AsyncMock(side_effect=_succeeding_persist)
        cache_mock = AsyncMock()

        with override_current_server(SERVER_IRAN), patch(
            "core.services.offer_overtime_preference_service.persist_overtime_preference",
            new=persist_mock,
        ), patch(
            "core.cache.invalidate_user_cache",
            new=cache_mock,
        ), patch.object(
            schemas,
            "UserOfferOvertimeUpdateResponse",
            wraps=schemas.UserOfferOvertimeUpdateResponse,
        ) as response_spy:
            with self.assertRaises(SentinelCommitError) as ctx:
                await update_my_offer_overtime(
                    payload=payload,
                    current_user=user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, sentinel_error)

        self.assertEqual(user.offer_overtime_minutes, 5)
        self.assertEqual(user.mutation_count, 1)

        persist_mock.assert_awaited_once_with(fake_db, user, payload.offer_overtime_minutes)
        self.assertEqual(persist_mock.await_count, 1)
        self.assertIs(persist_mock.call_args[0][0], fake_db)
        self.assertIs(persist_mock.call_args[0][1], user)
        self.assertEqual(persist_mock.call_args[0][2], 5)

        fake_db.commit.assert_awaited_once_with()
        self.assertEqual(fake_db.commit.await_count, 1)
        self.assertEqual(fake_db.commit.call_args, unittest.mock.call())

        self.assertEqual(order_tracker, ["persist", "commit"])
        persist_idx = order_tracker.index("persist")
        commit_idx = order_tracker.index("commit")
        self.assertLess(persist_idx, commit_idx)

        fake_db.refresh.assert_not_called()
        self.assertEqual(fake_db.refresh.await_count, 0)
        fake_db.rollback.assert_not_called()
        self.assertEqual(fake_db.rollback.await_count, 0)
        cache_mock.assert_not_called()
        self.assertEqual(cache_mock.await_count, 0)
        response_spy.assert_not_called()
        self.assertEqual(response_spy.call_count, 0)

        self.assertNotIn("refresh", order_tracker)
        self.assertNotIn("rollback", order_tracker)


if __name__ == "__main__":
    unittest.main()
