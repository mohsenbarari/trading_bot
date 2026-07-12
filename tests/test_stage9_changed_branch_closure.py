from __future__ import annotations

import logging
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers import link_account, start
from core.logging_config import _remove_all_handlers


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class Stage9ChangedProductBranchTests(unittest.IsolatedAsyncioTestCase):
    async def test_returning_panel_omits_unavailable_channel_and_web_links(self):
        user = SimpleNamespace(id=7)
        with patch.object(
            link_account,
            "build_channel_access_text",
            new=AsyncMock(return_value=""),
        ), patch.object(link_account, "build_webapp_plain_link_line", return_value=""):
            message = await link_account.build_returning_account_panel_message(
                MagicMock(), user
            )
        self.assertNotIn("http", message)
        self.assertIn("دکمه‌های منو", message)

    async def test_onboarding_setup_rejects_user_disappearing_under_lock(self):
        session = MagicMock()
        session.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=None)
            )
        )
        user = SimpleNamespace(id=9, telegram_id=12345)
        with patch.object(
            start,
            "AsyncSessionLocal",
            return_value=_AsyncContext(session),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "registration_onboarding_user_missing"
            ):
                await start._ensure_registration_onboarding(user)
        session.commit.assert_not_called()

    async def test_final_tutorial_ack_survives_welcome_delivery_failure(self):
        completed_step = start.BOT_ONBOARDING_REQUIRED_STEP
        db_user = SimpleNamespace(
            role="عادی",
            bot_onboarding_required_step=completed_step,
            bot_onboarding_completed_step=completed_step - 1,
            bot_onboarding_completed_at=None,
        )
        session = MagicMock()
        session.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=db_user)
            )
        )
        session.commit = AsyncMock()
        message = SimpleNamespace(
            chat=SimpleNamespace(id=77),
            edit_text=AsyncMock(),
            answer=AsyncMock(side_effect=RuntimeError("telegram unavailable")),
        )
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=12345),
            answer=AsyncMock(),
            message=message,
            bot=MagicMock(),
        )
        with patch.object(
            start,
            "AsyncSessionLocal",
            return_value=_AsyncContext(session),
        ), patch.object(
            start,
            "pending_onboarding_step",
            return_value=completed_step,
        ), patch.object(
            start,
            "build_linked_account_panel_message",
            new=AsyncMock(return_value="welcome"),
        ):
            await start._handle_bot_onboarding_ack(
                callback,
                SimpleNamespace(id=9),
                acknowledged_step=completed_step,
            )
        session.commit.assert_awaited_once()
        callback.answer.assert_awaited_with("ثبت شد.")
        message.answer.assert_awaited_once()


class _BrokenCloseHandler(logging.Handler):
    def emit(self, record):
        return None

    def close(self):
        raise RuntimeError("close failed")


class Stage9LoggingBranchTests(unittest.TestCase):
    def test_remove_all_handlers_ignores_close_failure_after_detach(self):
        logger = logging.Logger("stage9-handler-close")
        handler = _BrokenCloseHandler()
        logger.addHandler(handler)
        _remove_all_handlers(logger)
        self.assertEqual(logger.handlers, [])


if __name__ == "__main__":
    unittest.main()
