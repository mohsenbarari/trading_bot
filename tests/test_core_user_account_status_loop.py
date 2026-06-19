import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from core.background_job_authority import BackgroundJobAuthorityError
from core import user_account_status_loop


class _AsyncSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class CoreUserAccountStatusLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_finalize_due_user_global_locks_returns_zero_when_nothing_changes(self):
        session = AsyncMock()
        with patch.object(user_account_status_loop, "AsyncSessionLocal", return_value=_AsyncSessionContext(session)), patch(
            "core.user_account_status_loop.mark_due_users_globally_locked",
            new=AsyncMock(return_value=0),
        ), patch("core.background_job_authority.current_server", return_value="iran"):
            result = await user_account_status_loop.finalize_due_user_global_locks()

        self.assertEqual(result, 0)
        session.commit.assert_not_awaited()

    async def test_finalize_due_user_global_locks_commits_when_users_are_marked(self):
        session = AsyncMock()
        with patch.object(user_account_status_loop, "AsyncSessionLocal", return_value=_AsyncSessionContext(session)), patch(
            "core.user_account_status_loop.mark_due_users_globally_locked",
            new=AsyncMock(return_value=3),
        ), patch("core.background_job_authority.current_server", return_value="iran"):
            result = await user_account_status_loop.finalize_due_user_global_locks()

        self.assertEqual(result, 3)
        session.commit.assert_awaited_once()

    async def test_finalize_due_user_global_locks_refuses_foreign_before_db_session(self):
        session_factory = AsyncMock()

        with patch.object(user_account_status_loop, "AsyncSessionLocal", session_factory), patch(
            "core.background_job_authority.current_server",
            return_value="foreign",
        ):
            with self.assertRaises(BackgroundJobAuthorityError):
                await user_account_status_loop.finalize_due_user_global_locks()

        session_factory.assert_not_called()

    async def test_finalize_due_user_messenger_blocks_reuses_global_lock_finalizer(self):
        with patch(
            "core.user_account_status_loop.finalize_due_user_global_locks",
            new=AsyncMock(return_value=7),
        ) as finalize_mock:
            result = await user_account_status_loop.finalize_due_user_messenger_blocks()

        self.assertEqual(result, 7)
        finalize_mock.assert_awaited_once_with()

    async def test_user_account_status_loop_refuses_foreign_before_first_cycle(self):
        with patch(
            "core.user_account_status_loop.finalize_due_user_global_locks",
            new=AsyncMock(),
        ) as finalize_mock, patch(
            "core.background_job_authority.current_server",
            return_value="foreign",
        ):
            with self.assertRaises(BackgroundJobAuthorityError):
                await user_account_status_loop.user_account_status_loop()

        finalize_mock.assert_not_awaited()

    async def test_user_account_status_loop_logs_errors_and_keeps_looping(self):
        async def cancel_sleep(_delay):
            raise asyncio.CancelledError()

        with patch(
            "core.user_account_status_loop.finalize_due_user_global_locks",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ), patch(
            "core.user_account_status_loop.asyncio.sleep",
            side_effect=cancel_sleep,
        ), patch.object(user_account_status_loop, "logger") as logger, patch(
            "core.background_job_authority.current_server",
            return_value="iran",
        ):
            with self.assertRaises(asyncio.CancelledError):
                await user_account_status_loop.user_account_status_loop()

        logger.error.assert_called_once()
