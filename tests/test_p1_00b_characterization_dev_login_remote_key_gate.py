"""Characterization tests for dev_login remote API-key gate."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from fastapi import HTTPException

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
    def __init__(self, execute_results=None, get_result=None):
        self.execute_results = list(execute_results or [])
        self.commit = AsyncMock()
        self.flush = AsyncMock(side_effect=self._flush)
        self.refresh = AsyncMock(side_effect=self._refresh)
        self.added = []
        self.add = MagicMock(side_effect=self._add)
        self.get_result = get_result
        self.executed = []
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, stmt):
        self.executed.append(stmt)
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    async def get(self, _model, _id):
        return self.get_result

    def _add(self, item):
        self.added.append(item)

    async def _flush(self):
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = 77

    async def _refresh(self, item):
        if getattr(item, "id", None) is None:
            item.id = 77
        return item


def make_request(headers=None, host="127.0.0.1"):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=host))


class P100bDevLoginRemoteKeyGateCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_dev_login_remote_valid_key_existing_user_success(self):
        remote_ip = "198.51.100.25"
        configured_dev_key = "remote-dev-key-secret-staging-pass"
        request = make_request(
            headers={"X-DEV-API-KEY": configured_dev_key, "x-platform": "web"},
            host=remote_ip,
        )

        existing_user = User(
            id=42,
            account_name="dev_existing_42",
            mobile_number="09999999999",
            full_name="کاربر توسعهدهنده (تست)",
            address="توسعهدهنده",
            role=UserRole.SUPER_ADMIN,
            home_server="iran",
        )
        db = FakeDB([FakeExecuteResult(existing_user)])
        fixed_now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        fixed_session_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
        stale_session = SimpleNamespace(
            id=uuid.UUID("99999999-8888-7777-6666-555555555555"),
            device_name="Dev Bypass Terminal",
        )

        with patch.object(auth.settings, "environment", "staging"), patch.object(
            auth.settings,
            "dev_api_key",
            configured_dev_key,
        ), patch(
            "api.routers.auth.create_refresh_token",
            return_value="test-refresh-token",
        ) as refresh_mock, patch(
            "api.routers.auth.create_access_token",
            return_value="test-access-token",
        ) as access_mock, patch(
            "api.routers.auth.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ) as mandatory_mock, patch(
            "api.routers.auth.hash_token",
            return_value="hashed-refresh-token",
        ) as hash_mock, patch(
            "api.routers.auth.uuid.uuid4",
            return_value=fixed_session_id,
        ), patch(
            "api.routers.auth.utc_now",
            return_value=fixed_now,
        ), patch(
            "api.routers.auth._login_home_server",
            return_value="foreign",
        ) as home_server_mock, patch(
            "api.routers.auth._clear_dev_bypass_sessions",
            wraps=auth._clear_dev_bypass_sessions,
        ) as clear_dev_sessions_mock, patch(
            "api.routers.auth.get_active_sessions",
            new=AsyncMock(return_value=[stale_session]),
        ) as get_active_sessions_mock, patch(
            "api.routers.auth.deactivate_session",
            new=AsyncMock(),
        ) as deactivate_mock, patch(
            "api.routers.auth.publish_session_revocation",
            new=AsyncMock(),
        ) as publish_revocation_mock:
            result = await dev_login(request, db=db)

        self.assertEqual(
            result,
            {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
                "token_type": "bearer",
                "user_id": 42,
                "role": UserRole.SUPER_ADMIN,
            },
        )

        self.assertEqual(len(db.executed), 1)
        db.execute.assert_awaited_once()
        compiled_query = str(db.executed[0]).lower()
        self.assertIn("users.mobile_number", compiled_query)
        self.assertIn(
            "09999999999",
            [str(v) for v in db.executed[0].compile().params.values()],
        )

        mandatory_mock.assert_awaited_once_with(db, user=existing_user)
        self.assertIs(mandatory_mock.await_args.kwargs["user"], existing_user)

        clear_dev_sessions_mock.assert_awaited_once_with(
            db,
            user_id=existing_user.id,
            clear_all_active=True,
        )
        get_active_sessions_mock.assert_awaited_once_with(db, existing_user.id)
        deactivate_mock.assert_awaited_once_with(db, stale_session)
        publish_revocation_mock.assert_awaited_once_with(
            existing_user.id,
            [stale_session],
        )

        self.assertEqual(len(db.added), 1)
        db.add.assert_called_once()
        session = db.added[0]
        self.assertIsInstance(session, UserSession)
        self.assertEqual(session.id, fixed_session_id)
        self.assertEqual(session.user_id, 42)
        self.assertEqual(session.device_name, "Dev Bypass Terminal")
        self.assertEqual(session.device_ip, remote_ip)
        self.assertEqual(session.platform, Platform.WEB)
        self.assertEqual(session.refresh_token_hash, "hashed-refresh-token")
        self.assertEqual(session.home_server, "foreign")
        self.assertTrue(session.is_primary)
        self.assertTrue(session.is_active)
        self.assertEqual(session.expires_at, fixed_now + timedelta(days=365))

        self.assertEqual(db.commit.await_count, 1)
        db.commit.assert_awaited_once()

        refresh_mock.assert_called_once_with(subject=42)
        hash_mock.assert_called_once_with("test-refresh-token")
        home_server_mock.assert_called_once_with(request)
        access_mock.assert_called_once_with(
            subject=42,
            expires_delta=timedelta(minutes=60),
            session_id=str(fixed_session_id),
            server_id="foreign",
        )

        self.assertFalse(any(isinstance(item, User) for item in db.added))
        self.assertNotIn(existing_user, db.added)
        db.flush.assert_not_awaited()
        db.refresh.assert_not_awaited()
        self.assertEqual(existing_user.home_server, "iran")

    async def test_dev_login_remote_missing_key_rejected_before_downstream_work(self):
        remote_ip = "198.51.100.33"
        configured_dev_key = "remote-dev-key-secret-staging-pass"
        request = make_request(headers={"x-platform": "web"}, host=remote_ip)
        db = FakeDB()

        with patch.object(auth.settings, "environment", "staging"), patch.object(
            auth.settings,
            "dev_api_key",
            configured_dev_key,
        ), patch(
            "api.routers.auth.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ) as mandatory_mock, patch(
            "api.routers.auth._clear_dev_bypass_sessions",
            new=AsyncMock(),
        ) as clear_dev_sessions_mock, patch(
            "api.routers.auth.get_active_sessions",
            new=AsyncMock(),
        ) as get_active_sessions_mock, patch(
            "api.routers.auth.deactivate_session",
            new=AsyncMock(),
        ) as deactivate_mock, patch(
            "api.routers.auth.publish_session_revocation",
            new=AsyncMock(),
        ) as publish_revocation_mock, patch(
            "api.routers.auth.UserSession",
            side_effect=AssertionError("UserSession must not be constructed"),
        ) as user_session_mock, patch(
            "api.routers.auth.create_refresh_token",
            new=MagicMock(),
        ) as refresh_mock, patch(
            "api.routers.auth.create_access_token",
            new=MagicMock(),
        ) as access_mock, patch(
            "api.routers.auth.hash_token",
            new=MagicMock(),
        ) as hash_mock, patch(
            "api.routers.auth.time.time",
            new=MagicMock(),
        ) as time_mock, patch(
            "api.routers.auth.uuid.uuid4",
            new=MagicMock(),
        ) as uuid_mock, patch(
            "api.routers.auth.utc_now",
            new=MagicMock(),
        ) as utc_now_mock, patch(
            "api.routers.auth._login_home_server",
            new=MagicMock(),
        ) as home_server_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await dev_login(request, db=db)

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(
            exc_info.exception.detail,
            "دسترسی فقط از محیط برنامه\u200cنویسی یا با کلید امکان\u200cپذیر است",
        )

        self.assertEqual(db.executed, [])
        db.execute.assert_not_awaited()
        self.assertEqual(db.added, [])
        db.add.assert_not_called()
        db.flush.assert_not_awaited()
        db.refresh.assert_not_awaited()
        db.commit.assert_not_awaited()
        mandatory_mock.assert_not_awaited()
        clear_dev_sessions_mock.assert_not_awaited()
        get_active_sessions_mock.assert_not_awaited()
        deactivate_mock.assert_not_awaited()
        publish_revocation_mock.assert_not_awaited()
        user_session_mock.assert_not_called()
        refresh_mock.assert_not_called()
        access_mock.assert_not_called()
        hash_mock.assert_not_called()
        time_mock.assert_not_called()
        uuid_mock.assert_not_called()
        utc_now_mock.assert_not_called()
        home_server_mock.assert_not_called()

    async def test_dev_login_remote_invalid_key_rejected_before_downstream_work(self):
        remote_ip = "198.51.100.44"
        configured_dev_key = "remote-dev-key-secret-staging-pass"
        request = make_request(
            headers={"X-DEV-API-KEY": "wrong-dev-key-value", "x-platform": "web"},
            host=remote_ip,
        )
        db = FakeDB()

        with patch.object(auth.settings, "environment", "staging"), patch.object(
            auth.settings,
            "dev_api_key",
            configured_dev_key,
        ), patch(
            "api.routers.auth.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ) as mandatory_mock, patch(
            "api.routers.auth._clear_dev_bypass_sessions",
            new=AsyncMock(),
        ) as clear_dev_sessions_mock, patch(
            "api.routers.auth.get_active_sessions",
            new=AsyncMock(),
        ) as get_active_sessions_mock, patch(
            "api.routers.auth.deactivate_session",
            new=AsyncMock(),
        ) as deactivate_mock, patch(
            "api.routers.auth.publish_session_revocation",
            new=AsyncMock(),
        ) as publish_revocation_mock, patch(
            "api.routers.auth.UserSession",
            side_effect=AssertionError("UserSession must not be constructed"),
        ) as user_session_mock, patch(
            "api.routers.auth.create_refresh_token",
            new=MagicMock(),
        ) as refresh_mock, patch(
            "api.routers.auth.create_access_token",
            new=MagicMock(),
        ) as access_mock, patch(
            "api.routers.auth.hash_token",
            new=MagicMock(),
        ) as hash_mock, patch(
            "api.routers.auth.time.time",
            new=MagicMock(),
        ) as time_mock, patch(
            "api.routers.auth.uuid.uuid4",
            new=MagicMock(),
        ) as uuid_mock, patch(
            "api.routers.auth.utc_now",
            new=MagicMock(),
        ) as utc_now_mock, patch(
            "api.routers.auth._login_home_server",
            new=MagicMock(),
        ) as home_server_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await dev_login(request, db=db)

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(
            exc_info.exception.detail,
            "دسترسی فقط از محیط برنامه\u200cنویسی یا با کلید امکان\u200cپذیر است",
        )

        self.assertEqual(db.executed, [])
        db.execute.assert_not_awaited()
        self.assertEqual(db.added, [])
        db.add.assert_not_called()
        db.flush.assert_not_awaited()
        db.refresh.assert_not_awaited()
        db.commit.assert_not_awaited()
        mandatory_mock.assert_not_awaited()
        clear_dev_sessions_mock.assert_not_awaited()
        get_active_sessions_mock.assert_not_awaited()
        deactivate_mock.assert_not_awaited()
        publish_revocation_mock.assert_not_awaited()
        user_session_mock.assert_not_called()
        refresh_mock.assert_not_called()
        access_mock.assert_not_called()
        hash_mock.assert_not_called()
        time_mock.assert_not_called()
        uuid_mock.assert_not_called()
        utc_now_mock.assert_not_called()
        home_server_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
