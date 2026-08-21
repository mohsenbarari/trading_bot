import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.enums import UserAccountStatus
from core.services import user_flag_service
from models.user import UserRole
from models.user_flag import UserFlag


def scalar_result(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


def scalars_result(values):
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


class UserFlagServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_replacement_counters_use_fixed_expiring_windows(self):
        class FakeRedis:
            def __init__(self):
                self.values = {}
                self.set_calls = []

            async def set(self, key, value, *, ex, nx):
                self.set_calls.append((key, value, ex, nx))
                if key in self.values:
                    return False
                self.values[key] = int(value)
                return True

            async def incr(self, key):
                self.values[key] += 1
                return self.values[key]

        redis = FakeRedis()
        with patch("bot.utils.redis_helpers.get_redis", new=AsyncMock(return_value=redis)):
            first = await user_flag_service._increment_session_replacement_counters(7)
            second = await user_flag_service._increment_session_replacement_counters(7)

        self.assertEqual(first, {"daily": 1, "weekly": 1, "monthly": 1})
        self.assertEqual(second, {"daily": 2, "weekly": 2, "monthly": 2})
        self.assertIn(("session_replace:7:daily", "1", 86400, True), redis.set_calls)

    async def test_first_replacement_below_threshold_does_not_create_flag(self):
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
        user = SimpleNamespace(id=7)
        with patch.object(
            user_flag_service,
            "_increment_session_replacement_counters",
            new=AsyncMock(return_value={"daily": 1, "weekly": 1, "monthly": 1}),
        ):
            result = await user_flag_service.record_session_replacement_activity(
                db,
                user=user,
                replaced_session_count=1,
                device_name="Safari",
                device_ip="192.0.2.1",
                platform="web",
                home_server="iran",
            )

        self.assertIsNone(result.flag)
        self.assertFalse(result.flag_created)
        db.execute.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_threshold_creates_explainable_flag_and_alerts_admins(self):
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_result(None)),
            add=Mock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )
        db.refresh.side_effect = lambda flag: setattr(flag, "id", 41)
        user = SimpleNamespace(id=7, account_name="coin", full_name="Coin User")
        now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

        with patch.object(
            user_flag_service,
            "_increment_session_replacement_counters",
            new=AsyncMock(return_value={"daily": 2, "weekly": 2, "monthly": 2}),
        ), patch.object(user_flag_service, "utc_now", return_value=now), patch.object(
            user_flag_service,
            "notify_super_admins_about_user_flag",
            new=AsyncMock(),
        ) as notify_mock:
            result = await user_flag_service.record_session_replacement_activity(
                db,
                user=user,
                replaced_session_count=1,
                device_name="Chrome on Android",
                device_ip="192.0.2.2",
                platform="web",
                home_server="iran",
            )

        self.assertTrue(result.flag_created)
        self.assertEqual(result.flag.flag_type, "session_replacement_frequency")
        self.assertEqual(result.flag.reason_code, "repeated_session_replacement")
        self.assertEqual(result.flag.details["counts"]["daily"], 2)
        self.assertEqual(result.flag.details["threshold_period"], "daily")
        self.assertEqual(result.flag.details["device_name"], "Chrome on Android")
        db.add.assert_called_once_with(result.flag)
        notify_mock.assert_awaited_once_with(db, flag=result.flag, flagged_user=user)

    async def test_existing_open_flag_is_updated_without_creating_another_case(self):
        existing = UserFlag(
            id=11,
            user_id=7,
            flag_type="session_replacement_frequency",
            reason_code="repeated_session_replacement",
            status="open",
            severity="warning",
            details={},
            trigger_count=2,
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_result(existing)),
            add=Mock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )
        user = SimpleNamespace(id=7, account_name="coin")
        with patch.object(
            user_flag_service,
            "_increment_session_replacement_counters",
            new=AsyncMock(return_value={"daily": 3, "weekly": 3, "monthly": 3}),
        ), patch.object(
            user_flag_service,
            "notify_super_admins_about_user_flag",
            new=AsyncMock(),
        ) as notify_mock:
            result = await user_flag_service.record_session_replacement_activity(
                db,
                user=user,
                replaced_session_count=1,
                device_name="Firefox",
                device_ip=None,
                platform="web",
                home_server="iran",
            )

        self.assertFalse(result.flag_created)
        self.assertEqual(existing.trigger_count, 3)
        self.assertEqual(existing.details["counts"]["daily"], 3)
        db.add.assert_not_called()
        notify_mock.assert_not_awaited()

    async def test_new_flag_notifies_all_active_super_admins_on_both_surfaces(self):
        admins = [
            SimpleNamespace(
                id=1,
                role=UserRole.SUPER_ADMIN,
                is_deleted=False,
                account_status=UserAccountStatus.ACTIVE,
                telegram_id=1001,
                sync_version=4,
            ),
            SimpleNamespace(
                id=2,
                role=UserRole.SUPER_ADMIN,
                is_deleted=False,
                account_status=UserAccountStatus.ACTIVE,
                telegram_id=None,
                sync_version=4,
            ),
        ]
        db = SimpleNamespace(execute=AsyncMock(return_value=scalars_result(admins)), commit=AsyncMock())
        flag = SimpleNamespace(
            id=41,
            flag_type="session_replacement_frequency",
            reason_code="repeated_session_replacement",
        )
        user = SimpleNamespace(id=7, account_name="coin", full_name="Coin User")

        with patch.object(
            user_flag_service, "create_user_notification", new=AsyncMock(),
        ) as web_mock, patch.object(
            user_flag_service, "enqueue_telegram_action_notification_once", new=AsyncMock(),
        ) as telegram_mock:
            await user_flag_service.notify_super_admins_about_user_flag(
                db, flag=flag, flagged_user=user,
            )

        self.assertEqual(web_mock.await_count, 2)
        self.assertEqual(web_mock.await_args_list[0].kwargs["extra_payload"]["route"], "/admin/users")
        telegram_mock.assert_awaited_once()
        self.assertEqual(telegram_mock.await_args.kwargs["recipient"].user_id, 1)
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
