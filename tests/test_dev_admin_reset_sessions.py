import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts import dev_admin


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class DevAdminResetSessionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_reset_sessions_forwards_to_home_server_and_cleans_local_state(self):
        args = SimpleNamespace(
            identity="09370809280",
            delete_session_rows=True,
            clear_login_limits=True,
        )
        user = SimpleNamespace(
            id=7,
            mobile_number="09370809280",
            account_name="mohsen",
            full_name="Mohsen",
            home_server="iran",
        )
        db_session = object()

        with patch("scripts.dev_admin.init_db", new=AsyncMock()), patch(
            "scripts.dev_admin.AsyncSessionLocal",
            return_value=FakeSessionContext(db_session),
        ), patch(
            "scripts.dev_admin.require_user",
            new=AsyncMock(return_value=user),
        ), patch(
            "scripts.dev_admin.current_server",
            return_value="foreign",
        ), patch(
            "scripts.dev_admin.forward_remote_session_reset",
            new=AsyncMock(return_value=(200, {"revoked_active_sessions": 2, "deleted_session_rows": 2, "deleted_redis_keys": 4})),
        ) as remote_reset_mock, patch(
            "scripts.dev_admin.reset_user_session_state",
            new=AsyncMock(return_value={"revoked_active_sessions": 0, "deleted_redis_keys": 1}),
        ) as local_reset_mock:
            await dev_admin.reset_sessions(args)

        remote_reset_mock.assert_awaited_once_with(user, "iran")
        local_reset_mock.assert_awaited_once_with(
            db_session,
            user_id=7,
            mobile_number="09370809280",
            delete_session_rows=True,
            clear_login_limits=True,
        )

    async def test_reset_sessions_fails_when_remote_home_server_reset_fails(self):
        args = SimpleNamespace(
            identity="09370809280",
            delete_session_rows=True,
            clear_login_limits=True,
        )
        user = SimpleNamespace(
            id=7,
            mobile_number="09370809280",
            account_name="mohsen",
            full_name="Mohsen",
            home_server="iran",
        )

        with patch("scripts.dev_admin.init_db", new=AsyncMock()), patch(
            "scripts.dev_admin.AsyncSessionLocal",
            return_value=FakeSessionContext(object()),
        ), patch(
            "scripts.dev_admin.require_user",
            new=AsyncMock(return_value=user),
        ), patch(
            "scripts.dev_admin.current_server",
            return_value="foreign",
        ), patch(
            "scripts.dev_admin.forward_remote_session_reset",
            new=AsyncMock(return_value=(503, {"detail": "down"})),
        ), patch(
            "scripts.dev_admin.reset_user_session_state",
            new=AsyncMock(),
        ) as local_reset_mock:
            with self.assertRaises(SystemExit) as exc_info:
                await dev_admin.reset_sessions(args)

        self.assertIn("home-server reset failed", str(exc_info.exception))
        local_reset_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
