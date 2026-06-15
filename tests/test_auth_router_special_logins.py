import hashlib
import hmac
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers import auth
from api.routers.auth import (
    WebAppLogin,
    dev_login,
    webapp_login,
)
from models.session import Platform
from models.user import UserRole


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
        self.get_result = get_result
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    async def get(self, _model, _id):
        return self.get_result

    def add(self, item):
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


def build_webapp_init_data(bot_token, user_payload, auth_date=None, hash_override=None):
    auth_date = auth_date if auth_date is not None else int(time.time())
    parsed_data = {
        "auth_date": str(auth_date),
        "query_id": "AAEAAAE",
        "user": json.dumps(user_payload, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    parsed_data["hash"] = hash_override or calculated_hash
    return urlencode(parsed_data)


class AuthRouterSpecialLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_dev_login_rejects_remote_requests_without_valid_dev_key(self):
        request = make_request(headers={}, host="8.8.8.8")

        with self.assertRaises(HTTPException) as exc_info:
            await dev_login(request, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(
            exc_info.exception.detail,
            "دسترسی فقط از محیط برنامه‌نویسی یا با کلید امکان‌پذیر است",
        )

    async def test_dev_login_bootstraps_dev_user_and_session(self):
        request = make_request(headers={"x-platform": "web"}, host="127.0.0.1")
        db = FakeDB([FakeExecuteResult(None)])
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        with patch("api.routers.auth.time.time", return_value=1700000000), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-token",
        ) as refresh_mock, patch(
            "api.routers.auth.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ) as mandatory_mock, patch(
            "api.routers.auth.get_active_sessions",
            new=AsyncMock(return_value=[]),
        ), patch(
            "api.routers.auth.publish_session_revocation",
            new=AsyncMock(),
        ), patch(
            "api.routers.auth.hash_token",
            return_value="hashed-refresh",
        ), patch("api.routers.auth.uuid.uuid4", return_value="session-uuid"), patch(
            "api.routers.auth.utc_now",
            return_value=now,
        ), patch(
            "api.routers.auth._login_home_server",
            return_value="foreign",
        ), patch(
            "api.routers.auth.create_access_token",
            return_value="access-token",
        ) as access_mock:
            result = await dev_login(request, db=db)

        self.assertEqual(len(db.added), 2)
        user = db.added[0]
        session = db.added[1]
        self.assertEqual(user.account_name, "dev_1700000000")
        self.assertEqual(user.mobile_number, "09999999999")
        self.assertEqual(user.role, UserRole.SUPER_ADMIN)
        self.assertEqual(user.home_server, "foreign")
        db.flush.assert_awaited_once()
        self.assertIs(mandatory_mock.await_args.kwargs["user"], user)
        self.assertEqual(session.user_id, 77)
        self.assertEqual(session.device_name, "Dev Bypass Terminal")
        self.assertEqual(session.device_ip, "127.0.0.1")
        self.assertEqual(session.platform, Platform.WEB)
        self.assertEqual(session.refresh_token_hash, "hashed-refresh")
        self.assertEqual(session.home_server, "foreign")
        self.assertTrue(session.is_primary)
        self.assertEqual(session.expires_at, now + timedelta(days=365))
        self.assertEqual(db.commit.await_count, 2)
        refresh_mock.assert_called_once_with(subject=77)
        access_mock.assert_called_once_with(
            subject=77,
            expires_delta=timedelta(minutes=60),
            session_id="session-uuid",
            server_id="foreign",
        )
        self.assertEqual(result["access_token"], "access-token")
        self.assertEqual(result["refresh_token"], "refresh-token")
        self.assertEqual(result["user_id"], 77)
        self.assertEqual(result["role"], UserRole.SUPER_ADMIN)

    async def test_dev_login_accepts_private_networks_and_reuses_existing_user(self):
        request = make_request(headers={"x-platform": "web"}, host="192.168.1.8")
        existing_user = SimpleNamespace(id=7, role=UserRole.SUPER_ADMIN, home_server="iran")
        db = FakeDB([FakeExecuteResult(existing_user)])

        with patch("api.routers.auth.create_refresh_token", return_value="refresh-token"), patch(
            "api.routers.auth.ensure_mandatory_channel_membership", new=AsyncMock()
        ) as mandatory_mock, patch("api.routers.auth.hash_token", return_value="hashed-refresh"), patch(
            "api.routers.auth.uuid.uuid4", return_value="session-uuid"
        ), patch("api.routers.auth.utc_now", return_value=datetime(2026, 1, 1, tzinfo=timezone.utc)), patch(
            "api.routers.auth._login_home_server", return_value="foreign"
        ), patch(
            "api.routers.auth.get_active_sessions", new=AsyncMock(return_value=[])
        ), patch(
            "api.routers.auth.publish_session_revocation", new=AsyncMock()
        ), patch("api.routers.auth.create_access_token", return_value="access-token"):
            result = await dev_login(request, db=db)

        self.assertEqual(existing_user.home_server, "foreign")
        mandatory_mock.assert_awaited_once_with(db, user=existing_user)
        self.assertEqual(db.commit.await_count, 1)
        self.assertEqual(result["user_id"], 7)

    async def test_webapp_login_requires_bot_token_and_invalid_payload_returns_auth_failed(self):
        request = make_request(headers={"user-agent": "Telegram"}, host="10.0.0.8")

        with patch.object(auth.settings, "bot_token", ""):
            with self.assertRaises(HTTPException) as exc_info:
                await webapp_login(WebAppLogin(init_data="x"), raw_request=request, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(exc_info.exception.detail, "Bot token not configured")

        with patch.object(auth.settings, "bot_token", "bot-token"):
            with self.assertRaises(HTTPException) as exc_info:
                await webapp_login(WebAppLogin(init_data="auth_date=1&hash=bad&user=%7B%7D"), raw_request=request, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Authentication failed")

    async def test_webapp_login_returns_auth_failed_for_unknown_or_deleted_user(self):
        request = make_request(headers={"user-agent": "Telegram"}, host="10.0.0.8")
        init_data = build_webapp_init_data("bot-token", {"id": 123})

        with patch.object(auth.settings, "bot_token", "bot-token"):
            with self.assertRaises(HTTPException) as exc_info:
                await webapp_login(WebAppLogin(init_data=init_data), raw_request=request, db=FakeDB([FakeExecuteResult(None)]))
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Authentication failed")

        deleted_user = SimpleNamespace(id=7, is_deleted=True)
        with patch.object(auth.settings, "bot_token", "bot-token"):
            with self.assertRaises(HTTPException) as exc_info:
                await webapp_login(WebAppLogin(init_data=init_data), raw_request=request, db=FakeDB([FakeExecuteResult(deleted_user)]))
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Authentication failed")

    async def test_webapp_login_returns_approval_required_or_tokens(self):
        request = make_request(headers={"x-device-name": "ignored", "user-agent": "Telegram"}, host="10.0.0.8")
        init_data = build_webapp_init_data("bot-token", {"id": 123})
        user = SimpleNamespace(id=7, telegram_id=123, is_deleted=False, home_server="iran")
        approval_request = SimpleNamespace(id="req-1", expires_at=datetime(2026, 1, 1, 12, 0, 0))

        with patch.object(auth.settings, "bot_token", "bot-token"), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-token",
        ), patch(
            "api.routers.auth.assert_login_allowed_for_server",
            new=AsyncMock(),
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"action": "approval_required", "request": approval_request}),
        ) as handle_session_mock:
            result = await webapp_login(WebAppLogin(init_data=init_data), raw_request=request, db=FakeDB([FakeExecuteResult(user)]))

        self.assertEqual(result["status"], "approval_required")
        self.assertEqual(result["login_request_id"], "req-1")
        self.assertEqual(handle_session_mock.await_args.kwargs["device_name"], "Telegram Mini App")
        self.assertEqual(handle_session_mock.await_args.kwargs["platform"], Platform.TELEGRAM_MINI_APP)
        self.assertEqual(handle_session_mock.await_args.kwargs["home_server"], auth.SERVER_FOREIGN)

        with patch.object(auth.settings, "bot_token", "bot-token"), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-token",
        ), patch(
            "api.routers.auth.assert_login_allowed_for_server",
            new=AsyncMock(),
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"action": "ok", "session": SimpleNamespace(id="session-1")}),
        ), patch(
            "api.routers.auth.create_access_token",
            return_value="access-token",
        ) as access_mock:
            result = await webapp_login(WebAppLogin(init_data=init_data), raw_request=request, db=FakeDB([FakeExecuteResult(user)]))

        access_mock.assert_called_once_with(subject=7, session_id="session-1", server_id=auth.SERVER_FOREIGN)
        self.assertEqual(
            result,
            {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "token_type": "bearer",
            },
        )

        with patch.object(auth.settings, "bot_token", "bot-token"), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-token",
        ), patch(
            "api.routers.auth.assert_login_allowed_for_server",
            new=AsyncMock(),
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"action": "blocked", "reason": "too many requests"}),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await webapp_login(WebAppLogin(init_data=init_data), raw_request=request, db=FakeDB([FakeExecuteResult(user)]))
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Authentication failed")

    async def test_webapp_login_returns_blocked_for_inactive_accounts(self):
        request = make_request(headers={"user-agent": "Telegram"}, host="10.0.0.8")
        init_data = build_webapp_init_data("bot-token", {"id": 123})
        user = SimpleNamespace(id=7, telegram_id=123, is_deleted=False, home_server="iran")

        with patch.object(auth.settings, "bot_token", "bot-token"), patch(
            "api.routers.auth.create_refresh_token",
            return_value="refresh-token",
        ), patch(
            "api.routers.auth.assert_login_allowed_for_server",
            new=AsyncMock(),
        ), patch(
            "api.routers.auth.handle_login_session",
            new=AsyncMock(return_value={"action": "blocked", "reason": auth.ACCOUNT_INACTIVE_BLOCK_REASON}),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await webapp_login(WebAppLogin(init_data=init_data), raw_request=request, db=FakeDB([FakeExecuteResult(user)]))

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "User is blocked")

    async def test_webapp_login_rejects_expired_payloads_and_generic_parse_failures(self):
        request = make_request(headers={"user-agent": "Telegram"}, host="10.0.0.8")
        expired_data = build_webapp_init_data("bot-token", {"id": 123}, auth_date=1)

        with patch.object(auth.settings, "bot_token", "bot-token"):
            with self.assertRaises(HTTPException) as exc_info:
                await webapp_login(WebAppLogin(init_data=expired_data), raw_request=request, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Authentication failed")

        init_data = build_webapp_init_data("bot-token", {"id": 123})
        with patch.object(auth.settings, "bot_token", "bot-token"), patch("api.routers.auth.json.loads", side_effect=RuntimeError("bad user json")):
            with self.assertRaises(HTTPException) as exc_info:
                await webapp_login(WebAppLogin(init_data=init_data), raw_request=request, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Authentication failed")


if __name__ == "__main__":
    unittest.main()
