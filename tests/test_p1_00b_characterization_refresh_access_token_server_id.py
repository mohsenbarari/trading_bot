import socket
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.auth import RefreshTokenRequest, Token, refresh_access_token
from core.config import settings
from core.enums import UserAccountStatus
from core.server_routing import SERVER_FOREIGN


class FakeExecuteResult:
    """In-memory fake query execution result."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    """Fail-closed AsyncSession fake recording statements and rejecting unexpected executes."""

    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.executed_statements = []
        self.commit = AsyncMock()

    async def execute(self, stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        self.executed_statements.append(stmt)
        return self.execute_results.pop(0)


class TestP100bCharacterizationRefreshAccessTokenServerId(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for server_id precedence in api.routers.auth.refresh_access_token."""

    def setUp(self):
        super().setUp()
        self._orig_connect = socket.socket.connect
        self._orig_connect_ex = socket.socket.connect_ex
        self._orig_create_connection = getattr(socket, "create_connection", None)

        def _forbidden_connect(sock_self, *args, **kwargs):
            raise AssertionError("Fail-closed socket boundary: socket connection forbidden in unit tests")

        def _forbidden_create_connection(*args, **kwargs):
            raise AssertionError("Fail-closed socket boundary: socket connection forbidden in unit tests")

        socket.socket.connect = _forbidden_connect
        socket.socket.connect_ex = _forbidden_connect
        if self._orig_create_connection is not None:
            socket.create_connection = _forbidden_create_connection

        self.addCleanup(self._restore_socket_boundary)

    def _restore_socket_boundary(self):
        socket.socket.connect = self._orig_connect
        socket.socket.connect_ex = self._orig_connect_ex
        if self._orig_create_connection is not None:
            socket.create_connection = self._orig_create_connection

    async def _assert_refresh_access_token_success(
        self,
        *,
        session_home_server,
        user_home_server,
        expected_server_id,
        user_id,
        session_id,
        refresh_token,
    ):
        pinned_now = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
        req = RefreshTokenRequest(refresh_token=refresh_token)
        payload = {"type": "refresh", "sub": user_id}

        user = SimpleNamespace(
            id=user_id,
            is_deleted=False,
            home_server=user_home_server,
            account_status=UserAccountStatus.ACTIVE,
        )
        session = SimpleNamespace(
            id=session_id,
            expires_at=pinned_now + timedelta(days=1),
            home_server=session_home_server,
            last_active_at=None,
        )
        db = FakeDB([FakeExecuteResult(user)])

        with patch("jose.jwt.decode", return_value=payload) as mock_jwt_decode, patch(
            "api.routers.auth.get_session_by_refresh_token",
            new=AsyncMock(return_value=session),
        ) as mock_get_session, patch(
            "core.utils.utc_now",
            return_value=pinned_now,
        ), patch(
            "api.routers.auth.create_access_token",
            return_value="minted-access-token",
        ) as mock_create_access_token:
            result = await refresh_access_token(req, db=db)

        # Pin time & exact jwt.decode call assertion
        mock_jwt_decode.assert_called_once_with(
            refresh_token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )

        # Exact awaited get_session_by_refresh_token assertion
        mock_get_session.assert_awaited_once_with(db, refresh_token)

        # Exactly one user-select execute and no extra executions
        self.assertEqual(len(db.executed_statements), 1)
        self.assertEqual(len(db.execute_results), 0)
        stmt = db.executed_statements[0]
        self.assertTrue(getattr(stmt, "is_select", False))
        self.assertIn("users", str(stmt).lower())

        # Exact create_access_token kwargs assertion
        mock_create_access_token.assert_called_once_with(
            subject=user_id,
            expires_delta=timedelta(minutes=60),
            session_id=str(session_id),
            server_id=expected_server_id,
        )
        self.assertIsInstance(mock_create_access_token.call_args.kwargs["session_id"], str)
        self.assertEqual(mock_create_access_token.call_args.kwargs["session_id"], str(session_id))

        # Session last_active_at updated to pinned time
        self.assertIs(session.last_active_at, pinned_now)
        self.assertEqual(session.last_active_at, pinned_now)

        # db.commit awaited exactly once
        db.commit.assert_awaited_once()

        # Returned token mapping preserves refresh token and bearer type
        expected_token = {
            "access_token": "minted-access-token",
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }
        self.assertEqual(result, expected_token)
        token_schema = Token(**result)
        self.assertEqual(token_schema.access_token, "minted-access-token")
        self.assertEqual(token_schema.refresh_token, refresh_token)
        self.assertEqual(token_schema.token_type, "bearer")

    async def test_server_id_precedence_truthy_session_wins_over_truthy_user(self):
        """Case 1: Truthy session.home_server wins over a different truthy user.home_server."""
        await self._assert_refresh_access_token_success(
            session_home_server="iran",
            user_home_server=SERVER_FOREIGN,
            expected_server_id="iran",
            user_id=101,
            session_id="sess-truthy-session-101",
            refresh_token="refresh-token-session-wins-101",
        )

    async def test_server_id_precedence_absent_session_uses_truthy_user(self):
        """Case 2: Absent session.home_server uses truthy user.home_server."""
        await self._assert_refresh_access_token_success(
            session_home_server=None,
            user_home_server="iran",
            expected_server_id="iran",
            user_id=202,
            session_id="sess-truthy-user-202",
            refresh_token="refresh-token-user-wins-202",
        )

    async def test_server_id_precedence_both_absent_uses_authoritative_server_foreign(self):
        """Case 3: Both absent values fall back to authoritative SERVER_FOREIGN."""
        await self._assert_refresh_access_token_success(
            session_home_server=None,
            user_home_server=None,
            expected_server_id=SERVER_FOREIGN,
            user_id=303,
            session_id="sess-both-absent-303",
            refresh_token="refresh-token-both-absent-303",
        )
