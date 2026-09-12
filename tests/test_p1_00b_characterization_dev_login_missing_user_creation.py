"""Characterization test for dev_login staging loopback missing user creation."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from api.routers import auth
from api.routers.auth import dev_login
from models.session import Platform, UserSession
from models.user import User, UserRole


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value


class FakeDB:
    def __init__(self, execute_results=None, get_result=None, events=None):
        self.execute_results = list(execute_results or [])
        self.events = events if events is not None else []
        self.commit = AsyncMock(side_effect=self._commit)
        self.flush = AsyncMock(side_effect=self._flush)
        self.refresh = AsyncMock(side_effect=self._refresh)
        self.added = []
        self.add = MagicMock(side_effect=self._add)
        self.get_result = get_result
        self.executed = []
        self.execute = AsyncMock(side_effect=self._execute)
        self.commit_count = 0
        self.flush_count = 0
        self.refresh_count = 0

    async def _execute(self, stmt):
        self.executed.append(stmt)
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    async def get(self, _model, _id):
        return self.get_result

    def _add(self, item):
        self.added.append(item)
        if isinstance(item, User):
            self.events.append(
                (
                    "db_add_user",
                    item,
                    getattr(item, "id", None),
                    getattr(item, "home_server", None),
                )
            )
        elif isinstance(item, UserSession):
            self.events.append(("db_add_session", item))
        else:
            self.events.append(("db_add", item))

    async def _flush(self):
        self.flush_count += 1
        user_home_server = None
        for item in self.added:
            if isinstance(item, User):
                user_home_server = getattr(item, "home_server", None)
            if getattr(item, "id", None) is None:
                item.id = 77
        self.events.append(("db_flush", user_home_server))

    async def _commit(self):
        self.commit_count += 1
        self.events.append(("db_commit", self.commit_count))

    async def _refresh(self, item):
        self.refresh_count += 1
        self.events.append(("db_refresh", item))
        if getattr(item, "id", None) is None:
            item.id = 77
        return item


def make_request(headers=None, host="127.0.0.1"):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=host))


class P100bDevLoginMissingUserCreationCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_dev_login_staging_loopback_no_key_missing_user_creation(self):
        request = make_request(headers={"x-platform": "web"}, host="127.0.0.1")
        self.assertNotIn("X-DEV-API-KEY", request.headers)

        events: list[tuple] = []
        db = FakeDB([FakeExecuteResult(None)], events=events)
        fixed_now = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
        fixed_session_id = uuid.UUID("22222222-3333-4444-5555-666666666666")

        def fake_login_home_server(req):
            events.append(("resolve_login_home_server", "foreign"))
            return "foreign"

        original_user_setattr = User.__setattr__

        def tracking_user_setattr(instance, name, value):
            if name == "home_server" and isinstance(instance, User):
                events.append(("assign_user_home_server", value))
            return original_user_setattr(instance, name, value)

        async def fake_ensure_mandatory(db_arg, *, user):
            events.append(
                (
                    "ensure_mandatory_channel_membership",
                    user,
                    user.id,
                    user.home_server,
                )
            )

        orig_clear_dev_bypass = auth._clear_dev_bypass_sessions

        async def recorded_clear_dev_bypass(db_arg, *, user_id, clear_all_active):
            events.append(("clear_dev_bypass_sessions", user_id, clear_all_active))
            return await orig_clear_dev_bypass(
                db_arg, user_id=user_id, clear_all_active=clear_all_active
            )

        async def recorded_get_active_sessions(db_arg, user_id):
            events.append(("get_active_sessions", user_id))
            return []

        def fake_create_refresh_token(*args, **kwargs):
            events.append(("create_refresh_token", kwargs.get("subject")))
            return "test-refresh-token"

        def fake_create_access_token(*args, **kwargs):
            events.append(("create_access_token", kwargs))
            return "test-access-token"

        home_server_mock = MagicMock(side_effect=fake_login_home_server)
        mandatory_mock = AsyncMock(side_effect=fake_ensure_mandatory)
        clear_dev_sessions_mock = AsyncMock(side_effect=recorded_clear_dev_bypass)
        get_active_sessions_mock = AsyncMock(side_effect=recorded_get_active_sessions)
        deactivate_mock = AsyncMock()
        publish_revocation_mock = AsyncMock()
        refresh_mock = MagicMock(side_effect=fake_create_refresh_token)
        hash_mock = MagicMock(return_value="hashed-refresh-token")
        access_mock = MagicMock(side_effect=fake_create_access_token)

        with patch.object(auth.settings, "environment", "staging"), patch.object(
            auth.settings,
            "dev_api_key",
            "configured-dev-api-key-value",
        ), patch(
            "api.routers.auth.constant_time_secret_equals",
            side_effect=AssertionError("constant_time_secret_equals must not be called"),
        ) as secret_mock, patch(
            "api.routers.auth.time.time",
            return_value=1700000000,
        ), patch(
            "api.routers.auth._login_home_server",
            home_server_mock,
        ), patch.object(
            User,
            "__setattr__",
            tracking_user_setattr,
        ), patch(
            "api.routers.auth.ensure_mandatory_channel_membership",
            mandatory_mock,
        ), patch(
            "api.routers.auth._clear_dev_bypass_sessions",
            clear_dev_sessions_mock,
        ), patch(
            "api.routers.auth.get_active_sessions",
            get_active_sessions_mock,
        ), patch(
            "api.routers.auth.deactivate_session",
            deactivate_mock,
        ), patch(
            "api.routers.auth.publish_session_revocation",
            publish_revocation_mock,
        ), patch(
            "api.routers.auth.create_refresh_token",
            refresh_mock,
        ), patch(
            "api.routers.auth.hash_token",
            hash_mock,
        ), patch(
            "api.routers.auth.uuid.uuid4",
            return_value=fixed_session_id,
        ), patch(
            "api.routers.auth.utc_now",
            return_value=fixed_now,
        ), patch(
            "api.routers.auth.create_access_token",
            access_mock,
        ):
            result = await dev_login(request, db=db)

        secret_mock.assert_not_called()

        self.assertEqual(len(db.executed), 1)
        db.execute.assert_awaited_once()
        compiled_query = str(db.executed[0]).lower()
        self.assertIn("users.mobile_number", compiled_query)
        self.assertIn(
            "09999999999",
            [str(v) for v in db.executed[0].compile().params.values()],
        )

        self.assertEqual(len(db.added), 2)
        self.assertEqual(db.add.call_count, 2)
        user = db.added[0]
        session = db.added[1]

        self.assertIsInstance(user, User)
        self.assertEqual(user.account_name, "dev_1700000000")
        self.assertEqual(user.mobile_number, "09999999999")
        self.assertEqual(user.full_name, "کاربر توسعه‌دهنده (تست)")
        self.assertEqual(user.address, "توسعه‌دهنده")
        self.assertEqual(user.role, UserRole.SUPER_ADMIN)
        self.assertEqual(user.home_server, "foreign")

        self.assertEqual(
            [event[0] for event in events],
            [
                "resolve_login_home_server",
                "db_add_user",
                "db_flush",
                "assign_user_home_server",
                "ensure_mandatory_channel_membership",
                "db_commit",
                "db_refresh",
                "clear_dev_bypass_sessions",
                "get_active_sessions",
                "create_refresh_token",
                "db_add_session",
                "db_commit",
                "create_access_token",
            ],
        )

        home_server_mock.assert_called_once_with(request)
        self.assertEqual(events[0], ("resolve_login_home_server", "foreign"))

        self.assertIs(events[1][1], user)
        self.assertIsNone(events[1][2])
        self.assertIsNone(events[1][3])

        self.assertEqual(events[2], ("db_flush", None))
        self.assertEqual(user.id, 77)

        self.assertEqual(events[3], ("assign_user_home_server", "foreign"))

        self.assertIs(events[4][1], user)
        self.assertEqual(events[4][2], 77)
        self.assertEqual(events[4][3], "foreign")
        mandatory_mock.assert_awaited_once_with(db, user=user)

        self.assertEqual(events[5], ("db_commit", 1))
        self.assertIs(events[6][1], user)
        db.refresh.assert_awaited_once_with(user)

        self.assertEqual(events[7], ("clear_dev_bypass_sessions", 77, True))
        clear_dev_sessions_mock.assert_awaited_once_with(
            db,
            user_id=77,
            clear_all_active=True,
        )
        self.assertEqual(events[8], ("get_active_sessions", 77))
        get_active_sessions_mock.assert_awaited_once_with(db, 77)
        deactivate_mock.assert_not_called()
        publish_revocation_mock.assert_not_called()

        self.assertEqual(events[9], ("create_refresh_token", 77))
        refresh_mock.assert_called_once_with(subject=77)
        hash_mock.assert_called_once_with("test-refresh-token")

        self.assertIs(events[10][1], session)
        self.assertIsInstance(session, UserSession)
        self.assertEqual(session.id, fixed_session_id)
        self.assertEqual(session.user_id, 77)
        self.assertEqual(session.device_name, "Dev Bypass Terminal")
        self.assertEqual(session.device_ip, "127.0.0.1")
        self.assertEqual(session.platform, Platform.WEB)
        self.assertEqual(session.refresh_token_hash, "hashed-refresh-token")
        self.assertEqual(session.home_server, "foreign")
        self.assertTrue(session.is_primary)
        self.assertTrue(session.is_active)
        self.assertEqual(session.expires_at, fixed_now + timedelta(days=365))

        self.assertEqual(events[11], ("db_commit", 2))

        self.assertEqual(events[12][0], "create_access_token")
        self.assertEqual(
            events[12][1],
            {
                "subject": 77,
                "expires_delta": timedelta(minutes=60),
                "session_id": str(fixed_session_id),
                "server_id": "foreign",
            },
        )
        access_mock.assert_called_once_with(
            subject=77,
            expires_delta=timedelta(minutes=60),
            session_id=str(fixed_session_id),
            server_id="foreign",
        )

        self.assertEqual(
            result,
            {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
                "token_type": "bearer",
                "user_id": 77,
                "role": UserRole.SUPER_ADMIN,
            },
        )

        self.assertEqual(db.flush.await_count, 1)
        self.assertEqual(db.refresh.await_count, 1)
        self.assertEqual(db.commit.await_count, 2)


if __name__ == "__main__":
    unittest.main()
