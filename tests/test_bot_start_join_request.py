import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import handle_channel_join_request, handle_customer_tutorial_ack, handle_offer_tutorial_ack
from core.enums import UserAccountStatus, UserRole


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, value):
        self.value = value
        self.commit = AsyncMock()

    async def execute(self, stmt):
        return FakeExecuteResult(self.value)


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_join_request(chat_id=100, user_id=7, user_chat_id=77):
    bot = SimpleNamespace(
        decline_chat_join_request=AsyncMock(),
        approve_chat_join_request=AsyncMock(),
        send_message=AsyncMock(),
    )
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=user_id),
        user_chat_id=user_chat_id,
        bot=bot,
    )


class BotStartJoinRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_join_request_ignores_unrelated_chat_and_declines_missing_or_blocked_users(self):
        join_request = make_join_request(chat_id=999)
        with patch("bot.handlers.start.settings.channel_id", 100):
            await handle_channel_join_request(join_request)
        join_request.bot.decline_chat_join_request.assert_not_awaited()

        join_request = make_join_request(chat_id=100)
        with patch("bot.handlers.start.settings.channel_id", 100), patch(
            "bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(None))
        ):
            await handle_channel_join_request(join_request)
        join_request.bot.decline_chat_join_request.assert_awaited_once_with(chat_id=100, user_id=7)
        join_request.bot.send_message.assert_awaited_once()

        blocked_user = SimpleNamespace(
            id=7,
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.INACTIVE,
            is_deleted=False,
        )
        join_request = make_join_request(chat_id=100)
        with patch("bot.handlers.start.settings.channel_id", 100), patch(
            "bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(blocked_user))
        ):
            await handle_channel_join_request(join_request)
        self.assertIn("غیرفعال است", join_request.bot.send_message.await_args.kwargs["text"])

    async def test_join_request_approve_and_notification_failures_are_tolerated(self):
        active_user = SimpleNamespace(
            id=7,
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
            bot_onboarding_required_step=0,
            bot_onboarding_completed_step=0,
        )
        join_request = make_join_request(chat_id=100)
        join_request.bot.send_message = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("bot.handlers.start.settings.channel_id", 100), patch(
            "bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(active_user))
        ), patch(
            "bot.handlers.start.logger.exception"
        ) as logger_mock:
            await handle_channel_join_request(join_request)
        join_request.bot.approve_chat_join_request.assert_awaited_once_with(chat_id=100, user_id=7)
        self.assertEqual(active_user.bot_onboarding_required_step, 2)
        self.assertIn("راهنمای سریع ثبت آفر", join_request.bot.send_message.await_args.kwargs["text"])
        self.assertIsNotNone(join_request.bot.send_message.await_args.kwargs.get("reply_markup"))
        logger_mock.assert_called_once()

    async def test_join_request_declines_user_with_pending_registration_activation(self):
        active_user = SimpleNamespace(
            id=7,
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
        )
        join_request = make_join_request(chat_id=100)
        with patch("bot.handlers.start.settings.channel_id", 100), patch(
            "bot.handlers.start.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(active_user)),
        ), patch(
            "bot.handlers.start._direct_registration_runtime_ready",
            return_value=True,
        ), patch(
            "bot.handlers.start.registration_activation_block_for_user",
            new=AsyncMock(return_value=SimpleNamespace(reason="pending_sync")),
        ), patch(
            "bot.handlers.start.evaluate_bot_access",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ):
            await handle_channel_join_request(join_request)

        join_request.bot.decline_chat_join_request.assert_awaited_once_with(
            chat_id=100,
            user_id=7,
        )
        join_request.bot.approve_chat_join_request.assert_not_awaited()
        self.assertIn("همگام", join_request.bot.send_message.await_args.kwargs["text"])

        join_request = make_join_request(chat_id=100)
        join_request.bot.send_message = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("bot.handlers.start.settings.channel_id", 100), patch(
            "bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(None))
        ), patch("bot.handlers.start.logger.exception") as logger_mock:
            await handle_channel_join_request(join_request)
        join_request.bot.decline_chat_join_request.assert_awaited_once_with(chat_id=100, user_id=7)
        logger_mock.assert_called_once()

    async def test_offer_tutorial_ack_shows_customer_tutorial_step(self):
        user = SimpleNamespace(
            id=7,
            telegram_id=7,
            is_deleted=False,
            bot_onboarding_required_step=2,
            bot_onboarding_completed_step=0,
            bot_onboarding_completed_at=None,
        )
        session = FakeSession(user)
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )

        with patch("bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(session)):
            await handle_offer_tutorial_ack(callback, user=user)

        self.assertEqual(user.bot_onboarding_required_step, 2)
        self.assertEqual(user.bot_onboarding_completed_step, 1)
        self.assertIsNone(user.bot_onboarding_completed_at)
        session.commit.assert_awaited_once()
        callback.answer.assert_awaited_once_with("مرحله بعد")
        callback.message.edit_text.assert_awaited_once()
        self.assertIn("راهنمای سریع مشتریان", callback.message.edit_text.await_args.args[0])
        self.assertIsNotNone(callback.message.edit_text.await_args.kwargs.get("reply_markup"))

    async def test_customer_tutorial_ack_marks_onboarding_completed(self):
        user = SimpleNamespace(
            id=7,
            telegram_id=7,
            is_deleted=False,
            role=UserRole.STANDARD,
            bot_onboarding_required_step=2,
            bot_onboarding_completed_step=1,
            bot_onboarding_completed_at=None,
        )
        session = FakeSession(user)
        sent_panel = SimpleNamespace(message_id=91)
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=77),
            edit_text=AsyncMock(),
            answer=AsyncMock(return_value=sent_panel),
        )
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            bot=message.bot,
            answer=AsyncMock(),
            message=message,
        )

        with patch(
            "bot.handlers.start.AsyncSessionLocal",
            return_value=FakeSessionContext(session),
        ), patch(
            "bot.handlers.start.build_linked_account_panel_message",
            new=AsyncMock(return_value="welcome panel"),
        ), patch(
            "bot.handlers.start.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="persistent menu"),
        ), patch(
            "bot.handlers.start._user_facing_webapp_url",
            return_value="https://app.example",
        ), patch("bot.handlers.start.set_anchor") as set_anchor:
            await handle_customer_tutorial_ack(callback, user=user)

        self.assertEqual(user.bot_onboarding_required_step, 2)
        self.assertEqual(user.bot_onboarding_completed_step, 2)
        self.assertIsNotNone(user.bot_onboarding_completed_at)
        session.commit.assert_awaited_once()
        callback.answer.assert_awaited_once_with("ثبت شد.")
        callback.message.edit_text.assert_awaited_once()
        self.assertIn("اکنون می‌توانید", callback.message.edit_text.await_args.args[0])
        callback.message.answer.assert_awaited_once_with(
            "welcome panel",
            reply_markup="persistent menu",
        )
        set_anchor.assert_called_once_with(77, 91)

    async def test_tutorial_ack_rejects_out_of_order_and_duplicate_callbacks(self):
        user = SimpleNamespace(
            id=7,
            telegram_id=7,
            is_deleted=False,
            role=UserRole.STANDARD,
            bot_onboarding_required_step=2,
            bot_onboarding_completed_step=0,
            bot_onboarding_completed_at=None,
        )
        session = FakeSession(user)
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            bot=SimpleNamespace(),
            answer=AsyncMock(),
            message=SimpleNamespace(
                bot=SimpleNamespace(),
                chat=SimpleNamespace(id=77),
                edit_text=AsyncMock(),
                answer=AsyncMock(),
            ),
        )

        with patch("bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(session)):
            await handle_customer_tutorial_ack(callback, user=user)

        self.assertEqual(user.bot_onboarding_completed_step, 0)
        session.commit.assert_not_awaited()
        callback.answer.assert_awaited_once_with(
            "این مرحله در حال حاضر قابل تایید نیست.",
            show_alert=True,
        )
        callback.message.edit_text.assert_not_awaited()
        callback.message.answer.assert_not_awaited()

        user.bot_onboarding_completed_step = 2
        callback.answer.reset_mock()
        with patch("bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(session)):
            await handle_customer_tutorial_ack(callback, user=user)

        session.commit.assert_not_awaited()
        callback.answer.assert_awaited_once_with(
            "این مرحله در حال حاضر قابل تایید نیست.",
            show_alert=True,
        )
        callback.message.edit_text.assert_not_awaited()
        callback.message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
