import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import types
from bot.handlers import link_account
from bot.handlers.link_account import (
    BOT_ACCOUNT_LINK_MOBILE_STATE_KEY,
    BOT_ACCOUNT_LINK_TOKEN_STATE_KEY,
    handle_address_completion,
    handle_contact,
)
from core.enums import UserAccountStatus, UserRole


class FakeState:
    def __init__(self, initial_data=None):
        self.cleared = 0
        self.data = dict(initial_data or {})
        self.states = []

    async def clear(self):
        self.cleared += 1

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def set_state(self, state):
        self.states.append(state)


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, user=None):
        self.user = user
        self.commits = 0
        self.rollbacks = 0
        self.executed_statements = []

    async def execute(self, stmt):
        self.executed_statements.append(stmt)
        return FakeExecuteResult(self.user)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def db_factory(db_instance):
    async def _gen():
        yield db_instance
    return _gen


def make_message(
    *,
    contact_user_id=10,
    phone="+989121111111",
    from_user_id=10,
    username="user_bound",
    full_name="Boundary User",
    text=None,
    bot=None,
):
    return SimpleNamespace(
        contact=SimpleNamespace(user_id=contact_user_id, phone_number=phone),
        from_user=SimpleNamespace(id=from_user_id, username=username, full_name=full_name),
        text=text,
        bot=bot or SimpleNamespace(),
        answer=AsyncMock(),
    )


def make_user(**overrides):
    data = {
        "id": 99,
        "telegram_id": None,
        "account_name": "boundary_acc",
        "username": "boundary_user",
        "full_name": "Boundary User",
        "mobile_number": "09121111111",
        "address": "تهران خیابان آزادی پلاک ۱۰",
        "role": UserRole.STANDARD,
        "account_status": UserAccountStatus.ACTIVE,
        "is_deleted": False,
        "has_bot_access": True,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class LinkAccountStateBoundariesCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_contact_without_link_token_returns_neutral_without_database(self):
        message = make_message(contact_user_id=10, phone="+989121111111", from_user_id=10)
        state = FakeState(initial_data={})
        mock_get_db = MagicMock()

        with patch("bot.handlers.link_account.get_db", new=mock_get_db):
            await handle_contact(message, state)

        mock_get_db.assert_not_called()
        self.assertEqual(state.cleared, 1)
        self.assertEqual(message.answer.await_count, 1)

        call_args, call_kwargs = message.answer.await_args
        expected_neutral = (
            "برای فعال\u200cسازی این مسیر، ابتدا ثبت\u200cنام را در سامانه کامل کنید "
            "و از همان\u200cجا وارد ربات شوید."
        )
        self.assertEqual(call_args[0], expected_neutral)
        self.assertIn("ثبت\u200cنام را در سامانه کامل کنید", call_args[0])
        self.assertIsInstance(call_kwargs.get("reply_markup"), types.ReplyKeyboardRemove)

    async def test_handle_contact_rejects_selected_account_phone_mismatch_before_side_effects(self):
        state = FakeState(initial_data={BOT_ACCOUNT_LINK_TOKEN_STATE_KEY: "valid-sample-link-token"})
        user = make_user(
            mobile_number="09129999999",
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
        )
        user_snapshot = dict(user.__dict__)
        db = FakeDB(user=user)
        message = make_message(contact_user_id=10, phone="+989121111111", from_user_id=10)

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), \
             patch("bot.handlers.link_account.bot_account_access_denial_reason") as mock_access_denial, \
             patch("bot.handlers.link_account.get_web_only_bot_access_reason", new=AsyncMock()) as mock_web_only, \
             patch("bot.handlers.link_account.finalize_account_link", new=AsyncMock()) as mock_finalize, \
             patch("bot.handlers.link_account.complete_account_link_via_iran", new=AsyncMock()) as mock_complete:
            await handle_contact(message, state)

        self.assertEqual(message.answer.await_count, 1)
        call_args, call_kwargs = message.answer.await_args
        self.assertIn("شماره تماس ارسال شده با حساب", call_args[0])
        self.assertIn("مطابقت ندارد", call_args[0])
        self.assertIsInstance(call_kwargs.get("reply_markup"), types.ReplyKeyboardRemove)
        self.assertEqual(state.cleared, 1)
        self.assertEqual(len(db.executed_statements), 1)
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 0)
        self.assertEqual(user.__dict__, user_snapshot)

        mock_access_denial.assert_not_called()
        mock_web_only.assert_not_called()
        mock_finalize.assert_not_called()
        mock_complete.assert_not_called()

    async def test_handle_address_completion_continues_already_linked_sync_v2_without_token_state(self):
        byte_exact_address = "  تهران خیابان آزادی پلاک ۱۰\u200cب  "
        user = make_user(
            id=42,
            telegram_id=77,
            mobile_number="09121111111",
            address="System Default",
        )
        user_snapshot = dict(user.__dict__)
        db = FakeDB(user=user)

        state = FakeState(initial_data={"link_user_id": 42})
        self.assertNotIn(BOT_ACCOUNT_LINK_TOKEN_STATE_KEY, state.data)
        self.assertNotIn(BOT_ACCOUNT_LINK_MOBILE_STATE_KEY, state.data)

        bot_instance = SimpleNamespace()
        message = make_message(
            from_user_id=77,
            text=byte_exact_address,
            bot=bot_instance,
        )

        projected_user = SimpleNamespace(
            id=42,
            telegram_id=77,
            mobile_number="09121111111",
            address=byte_exact_address,
            full_name="Projected User",
        )

        mock_complete = AsyncMock(return_value=projected_user)
        mock_panel = AsyncMock(return_value="panel-content-sentinel")
        mock_public_url = MagicMock(return_value="https://iran.example.com/webapp")
        mock_keyboard = AsyncMock(return_value="keyboard-markup-sentinel")

        with patch.object(link_account, "settings", SimpleNamespace(registration_sync_v2_enabled=True)), \
             patch("bot.handlers.link_account.get_db", new=db_factory(db)), \
             patch("bot.handlers.link_account.bot_account_access_denial_reason", return_value=None) as mock_access_denial, \
             patch("bot.handlers.link_account.get_web_only_bot_access_reason", new=AsyncMock(return_value=None)) as mock_web_only, \
             patch("bot.handlers.link_account.complete_account_link_via_iran", new=mock_complete), \
             patch("bot.handlers.link_account.build_linked_account_panel_message", new=mock_panel), \
             patch("bot.handlers.link_account.public_webapp_url_for_links", new=mock_public_url), \
             patch("bot.handlers.link_account.build_persistent_navigation_keyboard", new=mock_keyboard):
            await handle_address_completion(message, state)

        self.assertEqual(db.rollbacks, 1)
        self.assertEqual(db.commits, 0)
        self.assertEqual(user.__dict__, user_snapshot)

        mock_access_denial.assert_called_once_with(user)
        mock_web_only.assert_awaited_once_with(db, user)

        mock_complete.assert_awaited_once_with(
            message=message,
            mobile_number="09121111111",
            link_token=None,
            address=byte_exact_address,
        )

        mock_panel.assert_awaited_once_with(
            bot_instance,
            projected_user,
            newly_linked=False,
            already_linked=True,
            address_registered=True,
        )

        mock_public_url.assert_called_once_with()
        mock_keyboard.assert_awaited_once_with(projected_user, "https://iran.example.com/webapp")

        message.answer.assert_awaited_once_with(
            "panel-content-sentinel",
            reply_markup="keyboard-markup-sentinel",
        )
        self.assertEqual(state.cleared, 1)


if __name__ == "__main__":
    unittest.main()
