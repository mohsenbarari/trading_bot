import unittest
from unittest.mock import AsyncMock, MagicMock

from bot.middlewares import auth as auth_middleware
from core.enums import UserAccountStatus


class FakeMessage:
    def __init__(self, user_id=None):
        self.from_user = MagicMock(id=user_id) if user_id is not None else None
        self.answer = AsyncMock()


class FakeCallbackQuery:
    def __init__(self, user_id=None, data=None):
        self.from_user = MagicMock(id=user_id) if user_id is not None else None
        self.data = data
        self.answer = AsyncMock()


class FakeUpdate:
    def __init__(self, message=None, callback_query=None, edited_message=None):
        self.message = message
        self.callback_query = callback_query
        self.edited_message = edited_message


class _AsyncSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _ExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class BotAuthMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_event_and_from_user_variants(self):
        original_message = auth_middleware.Message
        original_callback = auth_middleware.CallbackQuery
        original_update = auth_middleware.Update
        try:
            auth_middleware.Message = FakeMessage
            auth_middleware.CallbackQuery = FakeCallbackQuery
            auth_middleware.Update = FakeUpdate

            message = FakeMessage(1)
            callback = FakeCallbackQuery(2)
            update = FakeUpdate(message=message)

            self.assertEqual(auth_middleware._get_event_and_from_user(update), (message, message.from_user))
            self.assertEqual(auth_middleware._get_event_and_from_user(callback), (callback, callback.from_user))
            self.assertEqual(auth_middleware._get_event_and_from_user(object()), (None, None))
        finally:
            auth_middleware.Message = original_message
            auth_middleware.CallbackQuery = original_callback
            auth_middleware.Update = original_update

    async def test_middleware_passes_through_without_telegram_user(self):
        middleware = auth_middleware.AuthMiddleware(session_pool=MagicMock())
        handler = AsyncMock(return_value='ok')

        original_message = auth_middleware.Message
        original_callback = auth_middleware.CallbackQuery
        original_update = auth_middleware.Update
        try:
            auth_middleware.Message = FakeMessage
            auth_middleware.CallbackQuery = FakeCallbackQuery
            auth_middleware.Update = FakeUpdate
            event = FakeUpdate()
            result = await middleware(handler, event, {})
        finally:
            auth_middleware.Message = original_message
            auth_middleware.CallbackQuery = original_callback
            auth_middleware.Update = original_update

        self.assertEqual(result, 'ok')
        handler.assert_awaited_once()

    async def test_middleware_injects_user_and_allows_handler(self):
        session = AsyncMock()
        user = MagicMock(
            has_bot_access=True,
            account_status=UserAccountStatus.ACTIVE,
            messenger_blocked_at=None,
            messenger_grace_expires_at=None,
        )
        session.execute = AsyncMock(return_value=_ExecuteResult(user))
        middleware = auth_middleware.AuthMiddleware(session_pool=MagicMock(return_value=_AsyncSessionContext(session)))
        handler = AsyncMock(return_value='allowed')

        original_message = auth_middleware.Message
        original_callback = auth_middleware.CallbackQuery
        original_update = auth_middleware.Update
        try:
            auth_middleware.Message = FakeMessage
            auth_middleware.CallbackQuery = FakeCallbackQuery
            auth_middleware.Update = FakeUpdate
            event = FakeMessage(10)
            data = {}
            result = await middleware(handler, event, data)
        finally:
            auth_middleware.Message = original_message
            auth_middleware.CallbackQuery = original_callback
            auth_middleware.Update = original_update

        self.assertEqual(result, 'allowed')
        self.assertIs(data['user'], user)
        handler.assert_awaited_once_with(event, data)

    async def test_middleware_ignores_legacy_bot_access_flag(self):
        legacy_restricted_user = MagicMock(
            has_bot_access=False,
            account_status=UserAccountStatus.ACTIVE,
            messenger_blocked_at=None,
            messenger_grace_expires_at=None,
        )
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_ExecuteResult(legacy_restricted_user))
        middleware = auth_middleware.AuthMiddleware(session_pool=MagicMock(return_value=_AsyncSessionContext(session)))
        handler = AsyncMock(return_value='allowed')

        original_message = auth_middleware.Message
        original_callback = auth_middleware.CallbackQuery
        original_update = auth_middleware.Update
        try:
            auth_middleware.Message = FakeMessage
            auth_middleware.CallbackQuery = FakeCallbackQuery
            auth_middleware.Update = FakeUpdate

            message = FakeMessage(11)
            callback = FakeCallbackQuery(12)
            result_message = await middleware(handler, message, {})
            result_callback = await middleware(handler, callback, {})
        finally:
            auth_middleware.Message = original_message
            auth_middleware.CallbackQuery = original_callback
            auth_middleware.Update = original_update

        self.assertEqual(result_message, 'allowed')
        self.assertEqual(result_callback, 'allowed')
        message.answer.assert_not_awaited()
        callback.answer.assert_not_awaited()
        self.assertEqual(handler.await_count, 2)

    async def test_middleware_blocks_inactive_messenger_users(self):
        blocked_user = MagicMock(
            has_bot_access=True,
            account_status=UserAccountStatus.INACTIVE,
            messenger_blocked_at=object(),
            messenger_grace_expires_at=None,
        )
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_ExecuteResult(blocked_user))
        middleware = auth_middleware.AuthMiddleware(session_pool=MagicMock(return_value=_AsyncSessionContext(session)))
        handler = AsyncMock()

        original_message = auth_middleware.Message
        try:
            auth_middleware.Message = FakeMessage
            message = FakeMessage(14)
            await middleware(handler, message, {})
        finally:
            auth_middleware.Message = original_message

        message.answer.assert_awaited_once()
        self.assertIn("غیرفعال", message.answer.await_args.args[0])
        handler.assert_not_awaited()

    async def test_middleware_shows_alert_for_locked_callback_queries(self):
        blocked_user = MagicMock(
            has_bot_access=True,
            account_status=UserAccountStatus.INACTIVE,
            messenger_blocked_at=object(),
            messenger_grace_expires_at=None,
        )
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_ExecuteResult(blocked_user))
        middleware = auth_middleware.AuthMiddleware(session_pool=MagicMock(return_value=_AsyncSessionContext(session)))
        handler = AsyncMock()

        original_callback = auth_middleware.CallbackQuery
        original_is_locked = auth_middleware.is_user_global_web_locked
        try:
            auth_middleware.CallbackQuery = FakeCallbackQuery
            auth_middleware.is_user_global_web_locked = MagicMock(return_value=True)
            callback = FakeCallbackQuery(18)
            await middleware(handler, callback, {})
        finally:
            auth_middleware.CallbackQuery = original_callback
            auth_middleware.is_user_global_web_locked = original_is_locked

        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.await_args.kwargs['show_alert'])
        self.assertIn('غیرفعال', callback.answer.await_args.args[0])
        handler.assert_not_awaited()

    async def test_middleware_blocks_bot_until_offer_tutorial_acknowledged(self):
        pending_user = MagicMock(
            has_bot_access=True,
            account_status=UserAccountStatus.ACTIVE,
            messenger_blocked_at=None,
            messenger_grace_expires_at=None,
            bot_onboarding_required_step=1,
            bot_onboarding_completed_step=0,
        )
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_ExecuteResult(pending_user))
        middleware = auth_middleware.AuthMiddleware(session_pool=MagicMock(return_value=_AsyncSessionContext(session)))
        handler = AsyncMock(return_value='blocked')

        original_message = auth_middleware.Message
        original_callback = auth_middleware.CallbackQuery
        try:
            auth_middleware.Message = FakeMessage
            auth_middleware.CallbackQuery = FakeCallbackQuery
            message = FakeMessage(21)
            callback = FakeCallbackQuery(21, data="trade_type:buy")

            result_message = await middleware(handler, message, {})
            result_callback = await middleware(handler, callback, {})
        finally:
            auth_middleware.Message = original_message
            auth_middleware.CallbackQuery = original_callback

        self.assertIsNone(result_message)
        self.assertIsNone(result_callback)
        self.assertIn("راهنمای سریع ثبت آفر", message.answer.await_args.args[0])
        self.assertIsNotNone(message.answer.await_args.kwargs.get("reply_markup"))
        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])
        handler.assert_not_awaited()

    async def test_middleware_allows_offer_tutorial_ack_callback(self):
        pending_user = MagicMock(
            has_bot_access=True,
            account_status=UserAccountStatus.ACTIVE,
            messenger_blocked_at=None,
            messenger_grace_expires_at=None,
            bot_onboarding_required_step=1,
            bot_onboarding_completed_step=0,
        )
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_ExecuteResult(pending_user))
        middleware = auth_middleware.AuthMiddleware(session_pool=MagicMock(return_value=_AsyncSessionContext(session)))
        handler = AsyncMock(return_value='allowed')

        original_callback = auth_middleware.CallbackQuery
        try:
            auth_middleware.CallbackQuery = FakeCallbackQuery
            callback = FakeCallbackQuery(21, data=auth_middleware.OFFER_TUTORIAL_ACK_CALLBACK)
            result = await middleware(handler, callback, {})
        finally:
            auth_middleware.CallbackQuery = original_callback

        self.assertEqual(result, 'allowed')
        handler.assert_awaited_once()
        callback.answer.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
