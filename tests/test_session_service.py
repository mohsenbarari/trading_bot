import unittest
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.services import session_service
from models.session import LoginRequestStatus, Platform
from core.enums import UserAccountStatus
from models.user import UserRole


def scalar_one_or_none_result(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


def scalar_one_result(value):
    result = Mock()
    result.scalar_one.return_value = value
    return result


class FakePipeline:
    def __init__(self, sink):
        self.sink = sink

    def incr(self, key):
        self.sink.append(("incr", key))

    def expire(self, key, ttl):
        self.sink.append(("expire", key, ttl))

    async def execute(self):
        self.sink.append(("execute",))


class FakeRedis:
    def __init__(self, values=None):
        self.values = values or {}
        self.pipeline_calls = []

    async def get(self, key):
        return self.values.get(key)

    def pipeline(self):
        pipe = FakePipeline(self.pipeline_calls)
        return pipe


class SessionServiceHelperTests(unittest.TestCase):
    def test_get_effective_max_sessions_clamps_regular_users_and_locks_admins(self):
        regular_low = SimpleNamespace(role=UserRole.STANDARD, max_sessions=0)
        regular_high = SimpleNamespace(role=UserRole.STANDARD, max_sessions=9)
        super_admin = SimpleNamespace(role=UserRole.SUPER_ADMIN, max_sessions=3)
        middle_manager = SimpleNamespace(role=UserRole.MIDDLE_MANAGER, max_sessions=2)

        self.assertEqual(session_service.get_effective_max_sessions(regular_low), 1)
        self.assertEqual(session_service.get_effective_max_sessions(regular_high), 3)
        self.assertEqual(session_service.get_effective_max_sessions(super_admin), 1)
        self.assertEqual(session_service.get_effective_max_sessions(middle_manager), 1)

    def test_calculate_threshold_scales_by_half_session_steps(self):
        self.assertEqual(session_service.calculate_threshold(4, 1), 4)
        self.assertEqual(session_service.calculate_threshold(4, 2), 6)
        self.assertEqual(session_service.calculate_threshold(4, 3), 8)


class HandleLoginSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.accountant_patch = patch(
            "core.services.session_service._is_accountant_user",
            new=AsyncMock(return_value=False),
        )
        self.mock_is_accountant = self.accountant_patch.start()

    async def asyncTearDown(self):
        self.accountant_patch.stop()

    async def test_revives_suspended_session_and_updates_runtime_fields(self):
        old_refresh = "old-refresh-token"
        new_refresh = "new-refresh-token"
        suspended = SimpleNamespace(
            refresh_token_hash=session_service.hash_token(old_refresh),
            device_name="Old Device",
            device_ip="10.0.0.1",
            platform=Platform.WEB,
            home_server="foreign",
            last_active_at=None,
            expires_at=datetime.utcnow() - timedelta(days=1),
            is_active=True,
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_one_or_none_result(suspended)),
            commit=AsyncMock(),
        )
        user = SimpleNamespace(id=7, role=UserRole.STANDARD, max_sessions=2)

        result = await session_service.handle_login_session(
            db,
            user,
            new_refresh,
            device_name="Pixel 9",
            device_ip="10.0.0.2",
            platform=Platform.ANDROID,
            suspended_refresh_token=old_refresh,
            home_server="iran",
        )

        self.assertEqual(result["action"], "session_created")
        self.assertIs(result["session"], suspended)
        self.assertEqual(suspended.refresh_token_hash, session_service.hash_token(new_refresh))
        self.assertEqual(suspended.device_name, "Pixel 9")
        self.assertEqual(suspended.device_ip, "10.0.0.2")
        self.assertEqual(suspended.platform, Platform.ANDROID)
        self.assertEqual(suspended.home_server, "iran")
        self.assertGreater(suspended.expires_at, datetime.utcnow() + timedelta(days=29))
        db.commit.assert_awaited_once()

    async def test_creates_primary_session_for_first_login(self):
        db = SimpleNamespace(commit=AsyncMock())
        user = SimpleNamespace(id=11, role=UserRole.STANDARD, max_sessions=3)
        created_session = SimpleNamespace(id=uuid.uuid4())

        with patch("core.services.session_service.get_active_sessions", AsyncMock(return_value=[])), \
             patch("core.services.session_service.create_session", AsyncMock(return_value=created_session)) as create_session:
            result = await session_service.handle_login_session(
                db,
                user,
                "refresh-1",
                device_name="Laptop",
                device_ip="127.0.0.1",
                platform=Platform.WEB,
                home_server="foreign",
            )

        self.assertEqual(result, {"action": "session_created", "session": created_session})
        create_session.assert_awaited_once_with(
            db,
            user.id,
            "refresh-1",
            "Laptop",
            "127.0.0.1",
            Platform.WEB,
            is_primary=True,
            home_server="foreign",
        )
        db.commit.assert_awaited_once()

    async def test_blocks_inactive_users_before_creating_any_session(self):
        db = SimpleNamespace(commit=AsyncMock())
        user = SimpleNamespace(id=13, role=UserRole.STANDARD, max_sessions=3, account_status=UserAccountStatus.INACTIVE)

        result = await session_service.handle_login_session(db, user, "refresh-inactive")

        self.assertEqual(result, {"action": "blocked", "reason": session_service.ACCOUNT_INACTIVE_BLOCK_REASON})
        db.commit.assert_not_awaited()

    async def test_creates_non_primary_session_when_under_limit(self):
        db = SimpleNamespace(commit=AsyncMock())
        user = SimpleNamespace(id=12, role=UserRole.STANDARD, max_sessions=3)
        created_session = SimpleNamespace(id=uuid.uuid4())
        active_sessions = [SimpleNamespace(id=uuid.uuid4(), is_primary=True)]

        with patch("core.services.session_service.get_active_sessions", AsyncMock(return_value=active_sessions)), \
             patch("core.services.session_service.create_session", AsyncMock(return_value=created_session)) as create_session:
            result = await session_service.handle_login_session(
                db,
                user,
                "refresh-2",
                device_name="Browser",
                device_ip=None,
                platform=Platform.WEB,
                home_server="foreign",
            )

        self.assertEqual(result, {"action": "session_created", "session": created_session})
        create_session.assert_awaited_once_with(
            db,
            user.id,
            "refresh-2",
            "Browser",
            None,
            Platform.WEB,
            is_primary=False,
            home_server="foreign",
        )
        db.commit.assert_awaited_once()

    async def test_web_only_users_keep_regular_session_policy(self):
        db = SimpleNamespace(commit=AsyncMock())
        user = SimpleNamespace(id=120, role=UserRole.STANDARD, max_sessions=3, has_bot_access=False)
        created_session = SimpleNamespace(id=uuid.uuid4())
        active_sessions = [SimpleNamespace(id=uuid.uuid4(), is_primary=True)]

        with patch("core.services.session_service.get_active_sessions", AsyncMock(return_value=active_sessions)), \
             patch("core.services.session_service.create_session", AsyncMock(return_value=created_session)) as create_session:
            result = await session_service.handle_login_session(
                db,
                user,
                "refresh-web-only",
                device_name="Web Browser",
                device_ip="127.0.0.2",
                platform=Platform.WEB,
                home_server="foreign",
            )

        self.assertEqual(result, {"action": "session_created", "session": created_session})
        create_session.assert_awaited_once_with(
            db,
            user.id,
            "refresh-web-only",
            "Web Browser",
            "127.0.0.2",
            Platform.WEB,
            is_primary=False,
            home_server="foreign",
        )
        db.commit.assert_awaited_once()

    async def test_accountant_users_stay_limited_to_one_active_session(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=scalar_one_or_none_result(None)), commit=AsyncMock(), add=Mock())
        user = SimpleNamespace(id=12, role=UserRole.STANDARD, max_sessions=3)
        fake_redis = FakeRedis({
            "session_req:12:daily": "0",
            "session_req:12:weekly": "0",
            "session_req:12:monthly": "0",
        })
        settings = SimpleNamespace(
            anti_abuse_daily_base=4,
            anti_abuse_weekly_base=10,
            anti_abuse_monthly_base=20,
        )
        self.mock_is_accountant.return_value = True

        with patch("core.services.session_service.get_active_sessions", AsyncMock(return_value=[SimpleNamespace(is_primary=True)])), \
             patch("bot.utils.redis_helpers.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("core.services.session_service.get_trading_settings_async", AsyncMock(return_value=settings)), \
             patch("core.services.session_service.publish_user_event", new=AsyncMock(), create=True):
            result = await session_service.handle_login_session(db, user, "refresh-acc")

        self.assertEqual(result["action"], "approval_required")

    async def test_blocks_when_anti_abuse_threshold_is_hit(self):
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
        user = SimpleNamespace(id=21, role=UserRole.STANDARD, max_sessions=2)
        active_sessions = [SimpleNamespace(), SimpleNamespace()]
        fake_redis = FakeRedis({"session_req:21:daily": "6", "session_req:21:weekly": "0", "session_req:21:monthly": "0"})
        settings = SimpleNamespace(
            anti_abuse_daily_base=4,
            anti_abuse_weekly_base=10,
            anti_abuse_monthly_base=20,
        )

        with patch("core.services.session_service.get_active_sessions", AsyncMock(return_value=active_sessions)), \
             patch("bot.utils.redis_helpers.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("core.services.session_service.get_trading_settings_async", AsyncMock(return_value=settings)):
            result = await session_service.handle_login_session(db, user, "refresh-3")

        self.assertEqual(result["action"], "blocked")
        self.assertIn("daily", result["reason"])
        db.execute.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_reuses_existing_pending_request_when_limit_is_hit(self):
        existing_request = SimpleNamespace(
            id=uuid.uuid4(),
            requester_device_name="Existing Device",
            requester_ip="10.10.10.10",
            expires_at=datetime.utcnow() + timedelta(seconds=90),
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_one_or_none_result(existing_request)),
            commit=AsyncMock(),
            add=Mock(),
        )
        user = SimpleNamespace(id=22, role=UserRole.STANDARD, max_sessions=1)
        fake_redis = FakeRedis({
            "session_req:22:daily": "0",
            "session_req:22:weekly": "0",
            "session_req:22:monthly": "0",
        })
        settings = SimpleNamespace(
            anti_abuse_daily_base=4,
            anti_abuse_weekly_base=10,
            anti_abuse_monthly_base=20,
        )

        with patch("core.services.session_service.get_active_sessions", AsyncMock(return_value=[SimpleNamespace()])), \
             patch("bot.utils.redis_helpers.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("core.services.session_service.get_trading_settings_async", AsyncMock(return_value=settings)), \
             patch("core.utils.publish_user_event", AsyncMock()) as publish_user_event:
            result = await session_service.handle_login_session(
                db,
                user,
                "refresh-4",
                device_name="New Device",
                device_ip="192.168.1.50",
            )

        self.assertEqual(result, {"action": "approval_required", "request": existing_request})
        db.commit.assert_not_awaited()
        db.add.assert_not_called()
        publish_user_event.assert_awaited_once()

    async def test_reuses_existing_pending_request_when_publish_fails(self):
        existing_request = SimpleNamespace(
            id=uuid.uuid4(),
            requester_device_name=None,
            requester_ip=None,
            expires_at=datetime.utcnow() + timedelta(seconds=90),
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_one_or_none_result(existing_request)),
            commit=AsyncMock(),
            add=Mock(),
        )
        user = SimpleNamespace(id=24, role=UserRole.STANDARD, max_sessions=1)
        fake_redis = FakeRedis({
            "session_req:24:daily": "0",
            "session_req:24:weekly": "0",
            "session_req:24:monthly": "0",
        })
        settings = SimpleNamespace(
            anti_abuse_daily_base=4,
            anti_abuse_weekly_base=10,
            anti_abuse_monthly_base=20,
        )

        with patch("core.services.session_service.get_active_sessions", AsyncMock(return_value=[SimpleNamespace()])), \
             patch("bot.utils.redis_helpers.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("core.services.session_service.get_trading_settings_async", AsyncMock(return_value=settings)), \
             patch("core.utils.publish_user_event", AsyncMock(side_effect=RuntimeError("pub down"))), \
             patch.object(session_service, "logger") as logger:
            result = await session_service.handle_login_session(
                db,
                user,
                "refresh-4b",
                device_name="Fallback Device",
                device_ip="192.168.1.51",
            )

        self.assertEqual(result, {"action": "approval_required", "request": existing_request})
        logger.warning.assert_called_once()

    async def test_creates_login_request_and_increments_all_period_counters(self):
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_one_or_none_result(None)),
            commit=AsyncMock(),
            add=Mock(),
        )
        user = SimpleNamespace(id=23, role=UserRole.STANDARD, max_sessions=1)
        fake_redis = FakeRedis({
            "session_req:23:daily": "0",
            "session_req:23:weekly": "0",
            "session_req:23:monthly": "0",
        })
        settings = SimpleNamespace(
            anti_abuse_daily_base=4,
            anti_abuse_weekly_base=10,
            anti_abuse_monthly_base=20,
        )

        with patch("core.services.session_service.get_active_sessions", AsyncMock(return_value=[SimpleNamespace(is_primary=True)])), \
             patch("bot.utils.redis_helpers.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("core.services.session_service.get_trading_settings_async", AsyncMock(return_value=settings)), \
             patch("core.utils.publish_user_event", AsyncMock()) as publish_user_event:
            result = await session_service.handle_login_session(
                db,
                user,
                "refresh-5",
                device_name="Queued Device",
                device_ip="172.16.0.10",
                home_server="iran",
            )

        self.assertEqual(result["action"], "approval_required")
        login_request = result["request"]
        self.assertEqual(login_request.user_id, user.id)
        self.assertEqual(login_request.requester_device_name, "Queued Device")
        self.assertEqual(login_request.requester_ip, "172.16.0.10")
        self.assertEqual(login_request.requester_home_server, "iran")
        self.assertEqual(login_request.status, LoginRequestStatus.PENDING)
        db.add.assert_called_once_with(login_request)
        db.commit.assert_awaited_once()
        publish_user_event.assert_awaited_once()
        self.assertEqual(fake_redis.pipeline_calls.count(("execute",)), 3)
        self.assertIn(("expire", "session_req:23:daily", 86400), fake_redis.pipeline_calls)
        self.assertIn(("expire", "session_req:23:weekly", 604800), fake_redis.pipeline_calls)
        self.assertIn(("expire", "session_req:23:monthly", 2592000), fake_redis.pipeline_calls)

    async def test_creates_login_request_even_when_publish_fails(self):
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_one_or_none_result(None)),
            commit=AsyncMock(),
            add=Mock(),
        )
        user = SimpleNamespace(id=25, role=UserRole.STANDARD, max_sessions=1)
        fake_redis = FakeRedis({
            "session_req:25:daily": "0",
            "session_req:25:weekly": "0",
            "session_req:25:monthly": "0",
        })
        settings = SimpleNamespace(
            anti_abuse_daily_base=4,
            anti_abuse_weekly_base=10,
            anti_abuse_monthly_base=20,
        )

        with patch("core.services.session_service.get_active_sessions", AsyncMock(return_value=[SimpleNamespace(is_primary=True)])), \
             patch("bot.utils.redis_helpers.get_redis", AsyncMock(return_value=fake_redis)), \
             patch("core.services.session_service.get_trading_settings_async", AsyncMock(return_value=settings)), \
             patch("core.utils.publish_user_event", AsyncMock(side_effect=RuntimeError("pub down"))), \
             patch.object(session_service, "logger") as logger:
            result = await session_service.handle_login_session(
                db,
                user,
                "refresh-5b",
                device_name="Queued Device",
                device_ip="172.16.0.11",
            )

        self.assertEqual(result["action"], "approval_required")
        db.commit.assert_awaited_once()
        logger.warning.assert_called_once()


class ApproveAndRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.accountant_patch = patch(
            "core.services.session_service._is_accountant_user",
            new=AsyncMock(return_value=False),
        )
        self.accountant_patch.start()

    async def asyncTearDown(self):
        self.accountant_patch.stop()

    async def test_approve_login_request_rejects_expired_pending_request(self):
        login_req = SimpleNamespace(
            id=uuid.uuid4(),
            status=LoginRequestStatus.PENDING,
            expires_at=datetime.utcnow() - timedelta(seconds=5),
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_one_or_none_result(login_req)),
            commit=AsyncMock(),
        )
        approver_session = SimpleNamespace(id=uuid.uuid4())

        result = await session_service.approve_login_request(db, login_req.id, approver_session, "refresh-6")

        self.assertEqual(result, {"error": "درخواست منقضی شده است"})
        self.assertEqual(login_req.status, LoginRequestStatus.EXPIRED)
        db.commit.assert_awaited_once()

    async def test_approve_login_request_rejects_inactive_user(self):
        request_id = uuid.uuid4()
        login_req = SimpleNamespace(
            id=request_id,
            user_id=41,
            status=LoginRequestStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(seconds=60),
        )
        inactive_user = SimpleNamespace(id=41, account_status=UserAccountStatus.INACTIVE)
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[scalar_one_or_none_result(login_req), scalar_one_or_none_result(inactive_user)]),
            commit=AsyncMock(),
        )
        approver_session = SimpleNamespace(id=uuid.uuid4())

        result = await session_service.approve_login_request(db, request_id, approver_session, "refresh-inactive")

        self.assertEqual(result, {"error": "حساب کاربری غیرفعال شده است"})
        self.assertEqual(login_req.status, LoginRequestStatus.PENDING)
        db.commit.assert_not_awaited()

    async def test_approve_login_request_evicts_newest_non_primary_and_creates_session(self):
        request_id = uuid.uuid4()
        login_req = SimpleNamespace(
            id=request_id,
            user_id=41,
            status=LoginRequestStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(seconds=60),
            requester_device_name="Requester Device",
            requester_ip="10.0.0.5",
            requester_home_server="iran",
            resolved_by_session_id=None,
        )
        user = SimpleNamespace(id=41, role=UserRole.STANDARD, max_sessions=2)
        primary = SimpleNamespace(id=uuid.uuid4(), is_primary=True)
        newest_non_primary = SimpleNamespace(id=uuid.uuid4(), is_primary=False)
        new_session = SimpleNamespace(id=uuid.uuid4())
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[scalar_one_or_none_result(login_req), scalar_one_or_none_result(user)]),
            commit=AsyncMock(),
        )
        approver_session = SimpleNamespace(id=uuid.uuid4())

        with patch("core.services.session_service.get_active_sessions", AsyncMock(return_value=[primary, newest_non_primary])), \
             patch("core.services.session_service.deactivate_session", AsyncMock()) as deactivate_session, \
             patch("core.services.session_service.create_session", AsyncMock(return_value=new_session)) as create_session, \
             patch("core.utils.publish_user_event", AsyncMock()) as publish_user_event:
            result = await session_service.approve_login_request(
                db,
                request_id,
                approver_session,
                "refresh-7",
                platform=Platform.ANDROID,
            )

        self.assertEqual(result, {"session": new_session})
        self.assertEqual(login_req.status, LoginRequestStatus.APPROVED)
        self.assertEqual(login_req.resolved_by_session_id, approver_session.id)
        deactivate_session.assert_awaited_once_with(db, newest_non_primary)
        create_session.assert_awaited_once_with(
            db,
            login_req.user_id,
            "refresh-7",
            "Requester Device",
            "10.0.0.5",
            Platform.ANDROID,
            is_primary=False,
            home_server="iran",
        )
        db.commit.assert_awaited_once()
        publish_user_event.assert_awaited_once()

    async def test_deactivate_active_sessions_respects_exclusion_and_flushes(self):
        keep_id = uuid.uuid4()
        revoke_id = uuid.uuid4()
        kept = SimpleNamespace(id=keep_id, is_active=True)
        revoked = SimpleNamespace(id=revoke_id, is_active=True)
        db = SimpleNamespace(flush=AsyncMock())

        with patch("core.services.session_service.get_active_sessions", AsyncMock(return_value=[kept, revoked])):
            result = await session_service.deactivate_active_sessions(db, 51, exclude_session_id=keep_id)

        self.assertEqual(result, [revoked])
        self.assertTrue(kept.is_active)
        self.assertFalse(revoked.is_active)
        db.flush.assert_awaited_once()

    async def test_force_clear_sessions_commits_and_publishes_revocation(self):
        revoked_sessions = [SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())]
        db = SimpleNamespace(commit=AsyncMock())

        with patch("core.services.session_service.deactivate_active_sessions", AsyncMock(return_value=revoked_sessions)), \
             patch("core.services.session_service.publish_session_revocation", AsyncMock()) as publish_session_revocation:
            count = await session_service.force_clear_sessions(db, 61)

        self.assertEqual(count, 2)
        db.commit.assert_awaited_once()
        publish_session_revocation.assert_awaited_once_with(61, revoked_sessions)

    async def test_publish_session_revocation_blacklists_every_revoked_session(self):
        revoked_sessions = [SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())]

        with patch("core.utils.publish_user_event", AsyncMock()) as publish_user_event, \
             patch("core.services.session_service.blacklist_session", AsyncMock()) as blacklist_session:
            await session_service.publish_session_revocation(71, revoked_sessions)

        publish_user_event.assert_awaited_once_with(71, "session:revoked", {"action": "check_session"})
        self.assertEqual(blacklist_session.await_count, 2)
        blacklist_session.assert_any_await(revoked_sessions[0].id)
        blacklist_session.assert_any_await(revoked_sessions[1].id)

    async def test_blacklist_session_writes_expected_redis_key(self):
        redis = SimpleNamespace(setex=AsyncMock())
        session_id = uuid.uuid4()

        with patch("bot.utils.redis_helpers.get_redis", AsyncMock(return_value=redis)):
            await session_service.blacklist_session(session_id)

        redis.setex.assert_awaited_once_with(
            f"session_blacklist:{session_id}",
            session_service.SESSION_BLACKLIST_TTL,
            "1",
        )

    async def test_is_session_blacklisted_returns_true_for_existing_key_and_false_on_error(self):
        redis = SimpleNamespace(exists=AsyncMock(return_value=1))
        session_id = "abc-123"

        with patch("bot.utils.redis_helpers.get_redis", AsyncMock(return_value=redis)):
            self.assertTrue(await session_service.is_session_blacklisted(session_id))

        with patch("bot.utils.redis_helpers.get_redis", AsyncMock(side_effect=RuntimeError("redis down"))):
            self.assertFalse(await session_service.is_session_blacklisted(session_id))


class SessionServiceAdditionalCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.accountant_patch = patch(
            "core.services.session_service._is_accountant_user",
            new=AsyncMock(return_value=False),
        )
        self.accountant_patch.start()

    async def asyncTearDown(self):
        self.accountant_patch.stop()

    async def test_lookup_and_creation_helpers(self):
        token = "refresh-token"
        hashed = session_service.hash_token(token)
        self.assertEqual(len(hashed), 64)
        self.assertNotEqual(hashed, token)

        active_a = SimpleNamespace(id=uuid.uuid4())
        active_b = SimpleNamespace(id=uuid.uuid4())
        db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=[active_a, active_b])))),
                    scalar_one_or_none_result(active_b),
                ]
            )
        )

        active_sessions = await session_service.get_active_sessions(db, 5)
        session_by_token = await session_service.get_session_by_refresh_token(db, token)

        self.assertEqual(active_sessions, [active_a, active_b])
        self.assertIs(session_by_token, active_b)

        create_db = SimpleNamespace(add=Mock(), flush=AsyncMock())
        session = await session_service.create_session(
            create_db,
            user_id=17,
            refresh_token=token,
            device_name="Pixel",
            device_ip="10.0.0.4",
            platform=Platform.ANDROID,
            is_primary=True,
            home_server="iran",
        )

        self.assertEqual(session.user_id, 17)
        self.assertEqual(session.device_name, "Pixel")
        self.assertEqual(session.device_ip, "10.0.0.4")
        self.assertEqual(session.platform, Platform.ANDROID)
        self.assertTrue(session.is_primary)
        self.assertTrue(session.is_active)
        self.assertEqual(session.home_server, "iran")
        self.assertEqual(session.refresh_token_hash, hashed)
        self.assertGreater(session.expires_at, datetime.utcnow() + timedelta(days=29))
        create_db.add.assert_called_once_with(session)
        create_db.flush.assert_awaited_once()

    async def test_deactivate_and_promote_helpers(self):
        session = SimpleNamespace(id=uuid.uuid4(), user_id=21, is_active=True)
        db = SimpleNamespace(flush=AsyncMock())

        with patch("core.services.session_service.blacklist_session", AsyncMock()) as blacklist_session:
            await session_service.deactivate_session(db, session)

        self.assertFalse(session.is_active)
        blacklist_session.assert_awaited_once_with(session.id)
        db.flush.assert_awaited_once()

        oldest = SimpleNamespace(id=uuid.uuid4(), is_primary=False)
        newest = SimpleNamespace(id=uuid.uuid4(), is_primary=False)
        db = SimpleNamespace(flush=AsyncMock())
        with patch("core.services.session_service.get_active_sessions", AsyncMock(return_value=[oldest, newest])):
            promoted = await session_service.promote_next_primary(db, 22)

        self.assertIs(promoted, oldest)
        self.assertTrue(oldest.is_primary)
        db.flush.assert_awaited_once()

        with patch("core.services.session_service.get_active_sessions", AsyncMock(return_value=[])):
            self.assertIsNone(await session_service.promote_next_primary(SimpleNamespace(flush=AsyncMock()), 22))

    async def test_approve_login_request_handles_missing_processed_and_primary_eviction(self):
        approver_session = SimpleNamespace(id=uuid.uuid4())
        request_id = uuid.uuid4()

        db = SimpleNamespace(execute=AsyncMock(return_value=scalar_one_or_none_result(None)), commit=AsyncMock())
        self.assertEqual(
            await session_service.approve_login_request(db, request_id, approver_session, "refresh-x"),
            {"error": "درخواست یافت نشد"},
        )

        processed_request = SimpleNamespace(
            id=request_id,
            status=LoginRequestStatus.REJECTED,
            expires_at=datetime.utcnow() + timedelta(seconds=30),
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_one_or_none_result(processed_request)),
            commit=AsyncMock(),
        )
        self.assertEqual(
            await session_service.approve_login_request(db, request_id, approver_session, "refresh-y"),
            {"error": "درخواست قبلاً پردازش شده است"},
        )

        pending_request = SimpleNamespace(
            id=request_id,
            user_id=41,
            status=LoginRequestStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(seconds=60),
            requester_device_name="Requester Device",
            requester_ip="10.0.0.8",
            requester_home_server="iran",
            resolved_by_session_id=None,
        )
        only_primary = SimpleNamespace(id=uuid.uuid4(), is_primary=True)
        user = SimpleNamespace(id=41, role=UserRole.STANDARD, max_sessions=1)
        new_session = SimpleNamespace(id=uuid.uuid4())
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[scalar_one_or_none_result(pending_request), scalar_one_or_none_result(user)]),
            commit=AsyncMock(),
        )

        with patch("core.services.session_service.get_active_sessions", AsyncMock(return_value=[only_primary])), \
             patch("core.services.session_service.deactivate_session", AsyncMock()) as deactivate_session, \
             patch("core.services.session_service.create_session", AsyncMock(return_value=new_session)) as create_session, \
             patch("core.utils.publish_user_event", AsyncMock(side_effect=RuntimeError("pub down"))), \
             patch.object(session_service, "logger") as logger:
            result = await session_service.approve_login_request(
                db,
                request_id,
                approver_session,
                "refresh-z",
                device_name="Ignored Device",
                device_ip="127.0.0.1",
                platform=Platform.WEB,
                home_server="foreign",
            )

        self.assertEqual(result, {"session": new_session})
        deactivate_session.assert_awaited_once_with(db, only_primary)
        create_session.assert_awaited_once_with(
            db,
            pending_request.user_id,
            "refresh-z",
            "Requester Device",
            "10.0.0.8",
            Platform.WEB,
            is_primary=True,
            home_server="foreign",
        )
        self.assertEqual(pending_request.status, LoginRequestStatus.APPROVED)
        self.assertEqual(pending_request.resolved_by_session_id, approver_session.id)
        logger.warning.assert_called_once()

    async def test_approve_login_request_handles_drained_active_session_list(self):
        class DrainingSessions:
            def __init__(self):
                self.len_calls = 0

            def __len__(self):
                self.len_calls += 1
                return 2 if self.len_calls == 1 else 0

            def __iter__(self):
                return iter(())

            def remove(self, _item):
                raise AssertionError("remove should not be called when list is drained before eviction")

        request_id = uuid.uuid4()
        approver_session = SimpleNamespace(id=uuid.uuid4())
        pending_request = SimpleNamespace(
            id=request_id,
            user_id=42,
            status=LoginRequestStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(seconds=60),
            requester_device_name="Requester Device",
            requester_ip="10.0.0.9",
            requester_home_server="foreign",
            resolved_by_session_id=None,
        )
        user = SimpleNamespace(id=42, role=UserRole.STANDARD, max_sessions=2)
        new_session = SimpleNamespace(id=uuid.uuid4())
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[scalar_one_or_none_result(pending_request), scalar_one_or_none_result(user)]),
            commit=AsyncMock(),
        )

        with patch("core.services.session_service.get_active_sessions", AsyncMock(return_value=DrainingSessions())), \
             patch("core.services.session_service.deactivate_session", AsyncMock()) as deactivate_session, \
             patch("core.services.session_service.create_session", AsyncMock(return_value=new_session)) as create_session, \
             patch("core.utils.publish_user_event", AsyncMock()):
            result = await session_service.approve_login_request(
                db,
                request_id,
                approver_session,
                "refresh-break",
            )

        self.assertEqual(result, {"session": new_session})
        deactivate_session.assert_not_awaited()
        create_session.assert_awaited_once_with(
            db,
            pending_request.user_id,
            "refresh-break",
            "Requester Device",
            "10.0.0.9",
            Platform.WEB,
            is_primary=True,
            home_server="foreign",
        )

    async def test_reject_logout_and_revocation_failure_paths(self):
        approver_session = SimpleNamespace(id=uuid.uuid4())
        request_id = uuid.uuid4()

        db = SimpleNamespace(execute=AsyncMock(return_value=scalar_one_or_none_result(None)), commit=AsyncMock())
        self.assertEqual(
            await session_service.reject_login_request(db, request_id, approver_session),
            {"error": "درخواست یافت نشد"},
        )

        processed_request = SimpleNamespace(id=request_id, status=LoginRequestStatus.APPROVED)
        db = SimpleNamespace(execute=AsyncMock(return_value=scalar_one_or_none_result(processed_request)), commit=AsyncMock())
        self.assertEqual(
            await session_service.reject_login_request(db, request_id, approver_session),
            {"error": "درخواست قبلاً پردازش شده است"},
        )

        pending_request = SimpleNamespace(
            id=request_id,
            user_id=52,
            status=LoginRequestStatus.PENDING,
            resolved_by_session_id=None,
        )
        db = SimpleNamespace(execute=AsyncMock(return_value=scalar_one_or_none_result(pending_request)), commit=AsyncMock())
        with patch("core.utils.publish_user_event", AsyncMock(side_effect=RuntimeError("pub down"))), patch.object(
            session_service, "logger"
        ) as logger:
            result = await session_service.reject_login_request(db, request_id, approver_session)

        self.assertEqual(result, {"success": True})
        self.assertEqual(pending_request.status, LoginRequestStatus.REJECTED)
        self.assertEqual(pending_request.resolved_by_session_id, approver_session.id)
        db.commit.assert_awaited_once()
        logger.warning.assert_called_once()

        active_session = SimpleNamespace(id=uuid.uuid4(), user_id=61, is_primary=True)
        db = SimpleNamespace(commit=AsyncMock())
        promoted = SimpleNamespace(id=uuid.uuid4())
        with patch("core.services.session_service.deactivate_session", AsyncMock()) as deactivate_session, \
             patch("core.services.session_service.promote_next_primary", AsyncMock(return_value=promoted)) as promote_next_primary, \
             patch("core.utils.publish_user_event", AsyncMock(side_effect=RuntimeError("pub down"))), \
             patch.object(session_service, "logger") as logger:
            result = await session_service.logout_session(db, active_session)

        self.assertIs(result, promoted)
        deactivate_session.assert_awaited_once_with(db, active_session)
        promote_next_primary.assert_awaited_once_with(db, active_session.user_id)
        db.commit.assert_awaited_once()
        logger.warning.assert_called_once()

        revoked = [SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())]
        with patch("core.utils.publish_user_event", AsyncMock(side_effect=RuntimeError("pub down"))), \
             patch("core.services.session_service.blacklist_session", AsyncMock()) as blacklist_session, \
             patch.object(session_service, "logger") as logger:
            await session_service.publish_session_revocation(77, revoked)

        self.assertEqual(blacklist_session.await_count, 2)
        logger.warning.assert_called_once()

    async def test_blacklist_failure_and_zero_blacklist_lookup(self):
        session_id = uuid.uuid4()
        with patch("bot.utils.redis_helpers.get_redis", AsyncMock(side_effect=RuntimeError("redis down"))), patch.object(
            session_service, "logger"
        ) as logger:
            await session_service.blacklist_session(session_id)

        logger.warning.assert_called_once()

        redis = SimpleNamespace(exists=AsyncMock(return_value=0))
        with patch("bot.utils.redis_helpers.get_redis", AsyncMock(return_value=redis)):
            self.assertFalse(await session_service.is_session_blacklisted("missing-session"))


if __name__ == "__main__":
    unittest.main()