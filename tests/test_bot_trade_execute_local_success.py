import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_execute import (
    _execute_confirmed_channel_trade_via_shared_command,
    handle_channel_trade,
    handle_channel_trade_public,
)
from models.offer_request import OfferRequestSourceSurface
from models.offer import OfferStatus, OfferType


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, offer, scalar_values=None):
        self.offer = offer
        self.scalar_values = list(scalar_values or [10000])
        self.added = []
        self.commits = 0
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        return FakeExecuteResult(self.offer)

    async def refresh(self, offer, attrs):
        return None

    async def scalar(self, stmt):
        return self.scalar_values.pop(0)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_callback(chat_id=200):
    return SimpleNamespace(
        id="cb1",
        from_user=SimpleNamespace(id=300),
        answer=AsyncMock(),
        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=50, edit_reply_markup=AsyncMock()),
    )


def make_offer():
    return SimpleNamespace(
        id=7,
        status=OfferStatus.ACTIVE,
        user_id=9,
        offer_type=OfferType.BUY,
        quantity=5,
        remaining_quantity=5,
        is_wholesale=False,
        lot_sizes=[2, 3],
        home_server=None,
        offer_public_id="ofr_bot_local_7",
        channel_message_id=77,
        price=150000,
        commodity_id=12,
        commodity=SimpleNamespace(name="سکه"),
        user=SimpleNamespace(account_name="owner", mobile_number="0912", telegram_id=999),
    )


class BotTradeExecuteLocalSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_channel_trade_delegates_confirmed_local_trade_to_shared_command(self):
        user = SimpleNamespace(id=5, telegram_id=555, mobile_number="0935", account_name="buyer", trading_restricted_until=None)
        offer = make_offer()
        session = FakeSession(offer)
        callback = make_callback(chat_id=200)
        bot = SimpleNamespace(send_message=AsyncMock())

        with patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, None)), patch(
            "bot.handlers.trade_execute.AsyncSessionLocal", return_value=FakeSessionContext(session)
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "bot.handlers.trade_execute.is_remote_home", return_value=False
        ), patch("bot.handlers.trade_execute.validate_offer_trade_amount", return_value=(True, None, 2, [2, 3])), patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=True)
        ), patch(
            "bot.handlers.trade_execute._execute_confirmed_channel_trade_via_shared_command",
            new=AsyncMock(),
        ) as shared_command_mock, patch(
            "bot.handlers.trade_execute.settings", SimpleNamespace(channel_id=-100, bot_username="botname")
        ):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=bot)

        shared_command_mock.assert_awaited_once()
        kwargs = shared_command_mock.await_args.kwargs
        self.assertIs(kwargs["callback"], callback)
        self.assertIs(kwargs["user"], user)
        self.assertIs(kwargs["bot"], bot)
        self.assertIs(kwargs["session"], session)
        self.assertIs(kwargs["offer"], offer)
        self.assertEqual(kwargs["actual_amount"], 2)
        self.assertEqual(session.commits, 0)
        self.assertEqual(session.added, [])
        self.assertIn("offers.id", session.statements[0])

    async def test_handle_channel_trade_preconfirmed_local_trade_skips_double_click(self):
        user = SimpleNamespace(id=5, telegram_id=555, mobile_number="0935", account_name="buyer", trading_restricted_until=None)
        offer = make_offer()
        session = FakeSession(offer)
        callback = make_callback(chat_id=200)
        bot = SimpleNamespace(send_message=AsyncMock())

        with patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, None)), patch(
            "bot.handlers.trade_execute.AsyncSessionLocal", return_value=FakeSessionContext(session)
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "bot.handlers.trade_execute.is_remote_home", return_value=False
        ), patch("bot.handlers.trade_execute.validate_offer_trade_amount", return_value=(True, None, 2, [2, 3])), patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=False)
        ) as double_click_mock, patch(
            "bot.handlers.trade_execute._execute_confirmed_channel_trade_via_shared_command",
            new=AsyncMock(),
        ) as shared_command_mock, patch(
            "bot.handlers.trade_execute.settings", SimpleNamespace(channel_id=-100, bot_username="botname")
        ):
            await handle_channel_trade(
                callback,
                SimpleNamespace(offer_id=7, amount=2),
                user=user,
                bot=bot,
                trade_contention_preconfirmed=True,
                trade_contention_pre_gated=True,
            )

        double_click_mock.assert_not_awaited()
        shared_command_mock.assert_awaited_once()
        self.assertTrue(shared_command_mock.await_args.kwargs["request_pre_gated"])

    async def test_public_channel_trade_callback_resolves_offer_by_public_identity(self):
        user = SimpleNamespace(id=5, telegram_id=555, mobile_number="0935", account_name="buyer", trading_restricted_until=None)
        offer = make_offer()
        session = FakeSession(offer)
        callback = make_callback(chat_id=200)
        bot = SimpleNamespace(send_message=AsyncMock())

        with patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, None)), patch(
            "bot.handlers.trade_execute.AsyncSessionLocal", return_value=FakeSessionContext(session)
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "bot.handlers.trade_execute.is_remote_home", return_value=False
        ), patch("bot.handlers.trade_execute.validate_offer_trade_amount", return_value=(True, None, 2, [2, 3])), patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=True)
        ), patch(
            "bot.handlers.trade_execute._execute_confirmed_channel_trade_via_shared_command",
            new=AsyncMock(),
        ) as shared_command_mock, patch(
            "bot.handlers.trade_execute.settings", SimpleNamespace(channel_id=-100, bot_username="botname")
        ):
            await handle_channel_trade_public(
                callback,
                SimpleNamespace(offer_public_id="ofr_bot_local_7", amount=2),
                user=user,
                bot=bot,
            )

        shared_command_mock.assert_awaited_once()
        self.assertIn("offers.offer_public_id", session.statements[0])

    async def test_confirmed_channel_trade_helper_passes_telegram_metadata_to_shared_command(self):
        user = SimpleNamespace(id=5, telegram_id=555, mobile_number="0935", account_name="buyer")
        offer = make_offer()
        session = FakeSession(offer)
        callback = make_callback(chat_id=200)
        bot = SimpleNamespace(send_message=AsyncMock())
        events: list[str] = []
        callback.answer.side_effect = lambda *args, **kwargs: events.append("answer")

        def record_background_task():
            events.append("background_task")

        async def execute_trade_side_effect(*args, **kwargs):
            kwargs["background_tasks"].add_task(record_background_task)
            return {"id": 88}

        with patch(
            "bot.handlers.trade_execute._execute_trade_authoritatively",
            new=AsyncMock(side_effect=execute_trade_side_effect),
        ) as execute_mock, patch(
            "bot.handlers.trade_execute.remove_trade_suggestion_record",
            new=AsyncMock(),
        ) as remove_mock, patch(
            "bot.handlers.trade_execute.current_server",
            return_value="foreign",
        ), patch(
            "bot.handlers.trade_execute.settings",
            SimpleNamespace(channel_id=-100, bot_username="botname"),
        ):
            await _execute_confirmed_channel_trade_via_shared_command(
                callback=callback,
                callback_data=SimpleNamespace(offer_id=7, amount=2),
                user=user,
                bot=bot,
                session=session,
                offer=offer,
                actual_amount=2,
                request_pre_gated=True,
            )

        execute_mock.assert_awaited_once()
        kwargs = execute_mock.await_args.kwargs
        self.assertEqual(kwargs["trade_data"].offer_id, 7)
        self.assertEqual(kwargs["trade_data"].offer_public_id, "ofr_bot_local_7")
        self.assertEqual(kwargs["trade_data"].quantity, 2)
        self.assertEqual(kwargs["trade_data"].idempotency_key, "telegram_callback:5:ofr_bot_local_7:2:remaining:5:50")
        self.assertEqual(kwargs["request_source_surface"], OfferRequestSourceSurface.TELEGRAM_BOT)
        self.assertEqual(kwargs["request_source_server"], "foreign")
        self.assertTrue(kwargs["request_pre_gated"])
        self.assertEqual(kwargs["context"].owner_user, user)
        self.assertEqual(user.role.name, "STANDARD")
        callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        remove_mock.assert_awaited_once_with(7, 200, 50)
        callback.answer.assert_awaited_once_with("معامله ثبت شد ✅", show_alert=False)
        self.assertEqual(events, ["answer", "background_task"])


if __name__ == "__main__":
    unittest.main()
