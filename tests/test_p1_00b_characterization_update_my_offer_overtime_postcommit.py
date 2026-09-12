"""Hermetic post-commit characterization tests for api.routers.auth.update_my_offer_overtime.

Freezes observable behavior and phase progression/suppression under P1-00 network isolation:
1. Refresh failure preserves single in-memory mutation and commit, precedes single refresh attempt,
   and suppresses rollback, cache invalidation, and response construction.
2. Telegram-present cache invalidation failure preserves mutation, commit, and refresh,
   invokes cache invalidation with exact telegram_id, and suppresses rollback and response construction.
3. Telegram-present success completes persist, mutation, commit, refresh, cache invalidation,
   and response construction in exact order without rollback, returning the expected schema response.
"""

from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.auth import update_my_offer_overtime
from core.enums import UserAccountStatus, UserRole
from core.server_routing import SERVER_IRAN, override_current_server
from core.services.offer_overtime_preference_service import (
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
            if self._order_tracker is not None:
                self._order_tracker.append("mutation")
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


class UpdateMyOfferOvertimePostcommitCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_my_offer_overtime_refresh_failure_preserves_mutation_and_suppresses_downstream_phases(self):
        class SentinelRefreshError(Exception):
            """Unique sentinel exception during database refresh."""

        sentinel_error = SentinelRefreshError("database refresh failed after preference commit")

        order_tracker: list[str] = []
        user = InstrumentedUser(
            order_tracker=order_tracker,
            id=7,
            telegram_id=998877,
            offer_overtime_minutes=0,
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
        )
        self.assertIsNotNone(user.telegram_id)
        self.assertEqual(user.telegram_id, 998877)
        self.assertEqual(user.role, UserRole.STANDARD)

        fake_db = FakeDb(order_tracker=order_tracker)

        async def _failing_refresh(instance):
            order_tracker.append("refresh")
            raise sentinel_error

        fake_db.refresh.side_effect = _failing_refresh

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

        async def _tracking_cache(telegram_id_arg):
            order_tracker.append("cache")

        cache_mock = AsyncMock(side_effect=_tracking_cache)

        original_response_cls = schemas.UserOfferOvertimeUpdateResponse

        def _record_response(*args, **kwargs):
            order_tracker.append("response")
            return original_response_cls(*args, **kwargs)

        response_spy = MagicMock(side_effect=_record_response)

        with override_current_server(SERVER_IRAN), patch(
            "core.services.offer_overtime_preference_service.persist_overtime_preference",
            new=persist_mock,
        ), patch(
            "core.cache.invalidate_user_cache",
            new=cache_mock,
        ), patch.object(
            schemas,
            "UserOfferOvertimeUpdateResponse",
            new=response_spy,
        ):
            with self.assertRaises(SentinelRefreshError) as ctx:
                await update_my_offer_overtime(
                    payload=payload,
                    current_user=user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, sentinel_error)

        persist_mock.assert_awaited_once_with(fake_db, user, payload.offer_overtime_minutes)
        self.assertEqual(persist_mock.await_count, 1)
        self.assertIs(persist_mock.call_args[0][0], fake_db)
        self.assertIs(persist_mock.call_args[0][1], user)
        self.assertEqual(persist_mock.call_args[0][2], 5)

        fake_db.commit.assert_awaited_once_with()
        self.assertEqual(fake_db.commit.await_count, 1)

        fake_db.refresh.assert_awaited_once_with(user)
        self.assertEqual(fake_db.refresh.await_count, 1)
        self.assertIs(fake_db.refresh.call_args[0][0], user)

        self.assertEqual(order_tracker, ["persist", "mutation", "commit", "refresh"])
        persist_idx = order_tracker.index("persist")
        mutation_idx = order_tracker.index("mutation")
        commit_idx = order_tracker.index("commit")
        refresh_idx = order_tracker.index("refresh")
        self.assertLess(persist_idx, mutation_idx)
        self.assertLess(mutation_idx, commit_idx)
        self.assertLess(commit_idx, refresh_idx)

        self.assertEqual(user.offer_overtime_minutes, 5)
        self.assertEqual(user.mutation_count, 1)

        fake_db.rollback.assert_not_called()
        self.assertEqual(fake_db.rollback.await_count, 0)
        self.assertNotIn("rollback", order_tracker)

        cache_mock.assert_not_called()
        self.assertEqual(cache_mock.await_count, 0)
        self.assertNotIn("cache", order_tracker)

        response_spy.assert_not_called()
        self.assertEqual(response_spy.call_count, 0)
        self.assertNotIn("response", order_tracker)

    async def test_update_my_offer_overtime_cache_invalidation_failure_preserves_mutation_and_suppresses_response(self):
        class SentinelCacheError(Exception):
            """Unique sentinel exception during cache invalidation."""

        sentinel_error = SentinelCacheError("cache invalidation failed after refresh")

        order_tracker: list[str] = []
        user = InstrumentedUser(
            order_tracker=order_tracker,
            id=7,
            telegram_id=998877,
            offer_overtime_minutes=0,
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
        )
        self.assertIsNotNone(user.telegram_id)
        self.assertEqual(user.telegram_id, 998877)
        self.assertEqual(user.role, UserRole.STANDARD)

        fake_db = FakeDb(order_tracker=order_tracker)

        payload = schemas.UserOfferOvertimeUpdate(offer_overtime_minutes=4)
        self.assertEqual(payload.offer_overtime_minutes, 4)

        save_result = OvertimePreferenceSaveResult(
            offer_overtime_minutes=4,
            detail="مهلت پیشنهاد با موفقیت ثبت شد.",
            warning=None,
        )

        async def _succeeding_persist(db_arg, user_arg, value_arg):
            order_tracker.append("persist")
            user_arg.offer_overtime_minutes = value_arg
            return save_result

        persist_mock = AsyncMock(side_effect=_succeeding_persist)

        async def _failing_cache(telegram_id_arg):
            order_tracker.append("cache")
            raise sentinel_error

        cache_mock = AsyncMock(side_effect=_failing_cache)

        original_response_cls = schemas.UserOfferOvertimeUpdateResponse

        def _record_response(*args, **kwargs):
            order_tracker.append("response")
            return original_response_cls(*args, **kwargs)

        response_spy = MagicMock(side_effect=_record_response)

        with override_current_server(SERVER_IRAN), patch(
            "core.services.offer_overtime_preference_service.persist_overtime_preference",
            new=persist_mock,
        ), patch(
            "core.cache.invalidate_user_cache",
            new=cache_mock,
        ), patch.object(
            schemas,
            "UserOfferOvertimeUpdateResponse",
            new=response_spy,
        ):
            with self.assertRaises(SentinelCacheError) as ctx:
                await update_my_offer_overtime(
                    payload=payload,
                    current_user=user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, sentinel_error)

        cache_mock.assert_awaited_once_with(998877)
        self.assertEqual(cache_mock.await_count, 1)
        self.assertEqual(cache_mock.call_args[0][0], user.telegram_id)

        persist_mock.assert_awaited_once_with(fake_db, user, payload.offer_overtime_minutes)
        self.assertEqual(persist_mock.await_count, 1)
        self.assertIs(persist_mock.call_args[0][0], fake_db)
        self.assertIs(persist_mock.call_args[0][1], user)
        self.assertEqual(persist_mock.call_args[0][2], 4)

        fake_db.commit.assert_awaited_once_with()
        self.assertEqual(fake_db.commit.await_count, 1)

        fake_db.refresh.assert_awaited_once_with(user)
        self.assertEqual(fake_db.refresh.await_count, 1)
        self.assertIs(fake_db.refresh.call_args[0][0], user)

        self.assertEqual(order_tracker, ["persist", "mutation", "commit", "refresh", "cache"])
        persist_idx = order_tracker.index("persist")
        mutation_idx = order_tracker.index("mutation")
        commit_idx = order_tracker.index("commit")
        refresh_idx = order_tracker.index("refresh")
        cache_idx = order_tracker.index("cache")
        self.assertLess(persist_idx, mutation_idx)
        self.assertLess(mutation_idx, commit_idx)
        self.assertLess(commit_idx, refresh_idx)
        self.assertLess(refresh_idx, cache_idx)

        self.assertEqual(user.offer_overtime_minutes, 4)
        self.assertEqual(user.mutation_count, 1)

        fake_db.rollback.assert_not_called()
        self.assertEqual(fake_db.rollback.await_count, 0)
        self.assertNotIn("rollback", order_tracker)

        response_spy.assert_not_called()
        self.assertEqual(response_spy.call_count, 0)
        self.assertNotIn("response", order_tracker)

    async def test_update_my_offer_overtime_telegram_present_success_phase_order_and_response(self):
        order_tracker: list[str] = []
        user = InstrumentedUser(
            order_tracker=order_tracker,
            id=7,
            telegram_id=998877,
            offer_overtime_minutes=0,
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
        )
        self.assertIsNotNone(user.telegram_id)
        self.assertEqual(user.telegram_id, 998877)
        self.assertEqual(user.role, UserRole.STANDARD)

        fake_db = FakeDb(order_tracker=order_tracker)

        payload = schemas.UserOfferOvertimeUpdate(offer_overtime_minutes=6)
        self.assertEqual(payload.offer_overtime_minutes, 6)

        save_result = OvertimePreferenceSaveResult(
            offer_overtime_minutes=6,
            detail="مهلت معامله به ۶ دقیقه با موفقیت ثبت گردید.",
            warning="توجه: تنظیم آزمایشی معتبر است.",
        )

        async def _succeeding_persist(db_arg, user_arg, value_arg):
            order_tracker.append("persist")
            user_arg.offer_overtime_minutes = value_arg
            return save_result

        persist_mock = AsyncMock(side_effect=_succeeding_persist)

        async def _succeeding_cache(telegram_id_arg):
            order_tracker.append("cache")
            return True

        cache_mock = AsyncMock(side_effect=_succeeding_cache)

        original_response_cls = schemas.UserOfferOvertimeUpdateResponse

        def _record_response(*args, **kwargs):
            order_tracker.append("response")
            return original_response_cls(*args, **kwargs)

        response_spy = MagicMock(side_effect=_record_response)

        with override_current_server(SERVER_IRAN), patch(
            "core.services.offer_overtime_preference_service.persist_overtime_preference",
            new=persist_mock,
        ), patch(
            "core.cache.invalidate_user_cache",
            new=cache_mock,
        ), patch.object(
            schemas,
            "UserOfferOvertimeUpdateResponse",
            new=response_spy,
        ):
            result = await update_my_offer_overtime(
                payload=payload,
                current_user=user,
                db=fake_db,
            )

        persist_mock.assert_awaited_once_with(fake_db, user, payload.offer_overtime_minutes)
        self.assertEqual(persist_mock.await_count, 1)
        self.assertIs(persist_mock.call_args[0][0], fake_db)
        self.assertIs(persist_mock.call_args[0][1], user)
        self.assertEqual(persist_mock.call_args[0][2], 6)

        self.assertEqual(user.offer_overtime_minutes, 6)
        self.assertEqual(user.mutation_count, 1)

        fake_db.commit.assert_awaited_once_with()
        self.assertEqual(fake_db.commit.await_count, 1)

        fake_db.refresh.assert_awaited_once_with(user)
        self.assertEqual(fake_db.refresh.await_count, 1)
        self.assertIs(fake_db.refresh.call_args[0][0], user)

        cache_mock.assert_awaited_once_with(998877)
        self.assertEqual(cache_mock.await_count, 1)
        self.assertEqual(cache_mock.call_args[0][0], 998877)
        self.assertEqual(cache_mock.call_args[0][0], user.telegram_id)

        fake_db.rollback.assert_not_called()
        self.assertEqual(fake_db.rollback.await_count, 0)
        self.assertNotIn("rollback", order_tracker)

        self.assertEqual(
            order_tracker,
            ["persist", "mutation", "commit", "refresh", "cache", "response"],
        )
        persist_idx = order_tracker.index("persist")
        mutation_idx = order_tracker.index("mutation")
        commit_idx = order_tracker.index("commit")
        refresh_idx = order_tracker.index("refresh")
        cache_idx = order_tracker.index("cache")
        response_idx = order_tracker.index("response")
        self.assertLess(persist_idx, mutation_idx)
        self.assertLess(mutation_idx, commit_idx)
        self.assertLess(commit_idx, refresh_idx)
        self.assertLess(refresh_idx, cache_idx)
        self.assertLess(cache_idx, response_idx)

        response_spy.assert_called_once_with(
            offer_overtime_minutes=save_result.offer_overtime_minutes,
            detail=save_result.detail,
            warning=save_result.warning,
        )
        self.assertEqual(response_spy.call_count, 1)

        self.assertIsInstance(result, schemas.UserOfferOvertimeUpdateResponse)
        self.assertIs(type(result), schemas.UserOfferOvertimeUpdateResponse)

        self.assertEqual(result.offer_overtime_minutes, save_result.offer_overtime_minutes)
        self.assertEqual(result.offer_overtime_minutes, 6)
        self.assertEqual(result.detail, save_result.detail)
        self.assertEqual(result.detail, "مهلت معامله به ۶ دقیقه با موفقیت ثبت گردید.")
        self.assertEqual(result.warning, save_result.warning)
        self.assertEqual(result.warning, "توجه: تنظیم آزمایشی معتبر است.")


if __name__ == "__main__":
    unittest.main()
