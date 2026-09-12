"""Characterization tests for api.routers.auth.update_my_address.

Covers whitespace normalization on success, observable execution order,
commit failure with in-memory persistence and phase suppression, and
refresh failure with phase suppression under P1-00 network isolation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.auth import update_my_address
from core.enums import UserAccountStatus, UserRole
import schemas


class InstrumentedUser:
    """Runtime-shaped user fake instrumented to record address assignment and read."""

    def __init__(self, order_tracker: list[str] | None = None, **overrides):
        self._order_tracker = order_tracker
        self._tracking_active = True
        self._response_read_recorded = False
        self._address_assignment_count = 0
        self._response_read_count = 0

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
            "address": None,
        }
        data.update(overrides)
        self._address = data.pop("address")
        self.__dict__.update(data)

    @property
    def address(self) -> str | None:
        if self._tracking_active:
            self._response_read_count += 1
            if self._order_tracker is not None and not self._response_read_recorded:
                self._response_read_recorded = True
                self._order_tracker.append("response_read")
        return self._address

    @address.setter
    def address(self, value: str | None) -> None:
        if self._tracking_active:
            self._address_assignment_count += 1
            if self._order_tracker is not None:
                self._order_tracker.append("address_assignment")
        self._address = value

    def stop_tracking(self) -> None:
        self._tracking_active = False

    @property
    def response_read_recorded(self) -> bool:
        return self._response_read_recorded

    @property
    def response_read_count(self) -> int:
        return self._response_read_count

    @property
    def address_assignment_count(self) -> int:
        return self._address_assignment_count


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


class UpdateMyAddressCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_my_address_success_strict_order_and_normalization(self):
        order_tracker: list[str] = []
        user = InstrumentedUser(
            order_tracker=order_tracker,
            id=7,
            address="آدرس قدیمی کاربر در تهران، خیابان انقلاب",
        )
        raw_address = "  \n  تهران، خیابان آزادی، پلاک ۱۲، واحد ۴  \t  "
        normalized_address = "تهران، خیابان آزادی، پلاک ۱۲، واحد ۴"
        payload = schemas.UserAddressUpdate(address=raw_address)
        self.assertEqual(payload.address, normalized_address)

        fake_db = FakeDb(order_tracker=order_tracker)

        result = await update_my_address(
            payload=payload,
            current_user=user,
            db=fake_db,
        )

        expected_order = [
            "address_assignment",
            "commit",
            "refresh",
            "response_read",
        ]
        self.assertEqual(order_tracker, expected_order)

        assign_idx = order_tracker.index("address_assignment")
        commit_idx = order_tracker.index("commit")
        refresh_idx = order_tracker.index("refresh")
        response_read_idx = order_tracker.index("response_read")

        self.assertLess(assign_idx, commit_idx)
        self.assertLess(commit_idx, refresh_idx)
        self.assertLess(refresh_idx, response_read_idx)

        self.assertEqual(user.address_assignment_count, 1)
        self.assertTrue(user.response_read_recorded)
        self.assertEqual(user.response_read_count, 1)

        fake_db.commit.assert_awaited_once_with()
        self.assertEqual(fake_db.commit.call_args, unittest.mock.call())
        self.assertEqual(fake_db.commit.await_count, 1)

        fake_db.refresh.assert_awaited_once_with(user)
        self.assertIs(fake_db.refresh.call_args[0][0], user)
        self.assertEqual(fake_db.refresh.await_count, 1)

        fake_db.rollback.assert_not_called()
        self.assertEqual(fake_db.rollback.await_count, 0)
        self.assertNotIn("rollback", order_tracker)

        user.stop_tracking()
        self.assertEqual(user.address, normalized_address)

        self.assertIsInstance(result, schemas.UserAddressUpdateResponse)
        self.assertEqual(result.address, normalized_address)

    async def test_update_my_address_commit_failure_preserves_state_and_suppresses_phases(self):
        class SentinelCommitError(Exception):
            pass

        sentinel_error = SentinelCommitError("database transaction failed during address commit")

        order_tracker: list[str] = []
        user = InstrumentedUser(
            order_tracker=order_tracker,
            id=7,
            address="آدرس قدیمی کاربر در تهران",
        )
        raw_address = "  \t  میدان ولیعصر، کوچه شقایق، پلاک ۷  \n "
        normalized_address = "میدان ولیعصر، کوچه شقایق، پلاک ۷"
        payload = schemas.UserAddressUpdate(address=raw_address)
        self.assertEqual(payload.address, normalized_address)

        fake_db = FakeDb(order_tracker=order_tracker)

        async def _failing_commit():
            order_tracker.append("commit")
            raise sentinel_error

        fake_db.commit.side_effect = _failing_commit

        with self.assertRaises(SentinelCommitError) as ctx:
            await update_my_address(
                payload=payload,
                current_user=user,
                db=fake_db,
            )

        self.assertIs(ctx.exception, sentinel_error)
        self.assertEqual(user.address_assignment_count, 1)
        self.assertFalse(user.response_read_recorded)
        self.assertEqual(user.response_read_count, 0)

        expected_order = ["address_assignment", "commit"]
        self.assertEqual(order_tracker, expected_order)
        self.assertLess(
            order_tracker.index("address_assignment"),
            order_tracker.index("commit"),
        )

        fake_db.commit.assert_awaited_once_with()
        self.assertEqual(fake_db.commit.call_args, unittest.mock.call())
        self.assertEqual(fake_db.commit.await_count, 1)

        fake_db.refresh.assert_not_called()
        self.assertEqual(fake_db.refresh.await_count, 0)
        fake_db.rollback.assert_not_called()
        self.assertEqual(fake_db.rollback.await_count, 0)

        self.assertNotIn("refresh", order_tracker)
        self.assertNotIn("rollback", order_tracker)
        self.assertNotIn("response_read", order_tracker)

        user.stop_tracking()
        self.assertEqual(user.address, normalized_address)

    async def test_update_my_address_refresh_failure_preserves_state_and_suppresses_phases(self):
        class SentinelRefreshError(Exception):
            pass

        sentinel_error = SentinelRefreshError("database session failed during entity refresh")

        order_tracker: list[str] = []
        user = InstrumentedUser(
            order_tracker=order_tracker,
            id=7,
            address="آدرس قدیمی کاربر در اصفهان",
        )
        raw_address = "  \t  اصفهان، خیابان چهارباغ عباسی، پلاک ۴۴  \n "
        normalized_address = "اصفهان، خیابان چهارباغ عباسی، پلاک ۴۴"
        payload = schemas.UserAddressUpdate(address=raw_address)
        self.assertEqual(payload.address, normalized_address)

        fake_db = FakeDb(order_tracker=order_tracker)

        async def _failing_refresh(instance):
            order_tracker.append("refresh")
            raise sentinel_error

        fake_db.refresh.side_effect = _failing_refresh

        with self.assertRaises(SentinelRefreshError) as ctx:
            await update_my_address(
                payload=payload,
                current_user=user,
                db=fake_db,
            )

        self.assertIs(ctx.exception, sentinel_error)
        self.assertEqual(user.address_assignment_count, 1)
        self.assertFalse(user.response_read_recorded)
        self.assertEqual(user.response_read_count, 0)

        expected_order = ["address_assignment", "commit", "refresh"]
        self.assertEqual(order_tracker, expected_order)
        self.assertLess(
            order_tracker.index("address_assignment"),
            order_tracker.index("commit"),
        )
        self.assertLess(
            order_tracker.index("commit"),
            order_tracker.index("refresh"),
        )

        fake_db.commit.assert_awaited_once_with()
        self.assertEqual(fake_db.commit.call_args, unittest.mock.call())
        self.assertEqual(fake_db.commit.await_count, 1)

        fake_db.refresh.assert_awaited_once_with(user)
        self.assertIs(fake_db.refresh.call_args[0][0], user)
        self.assertEqual(fake_db.refresh.await_count, 1)

        fake_db.rollback.assert_not_called()
        self.assertEqual(fake_db.rollback.await_count, 0)

        self.assertNotIn("rollback", order_tracker)
        self.assertNotIn("response_read", order_tracker)

        user.stop_tracking()
        self.assertEqual(user.address, normalized_address)


if __name__ == "__main__":
    unittest.main()
