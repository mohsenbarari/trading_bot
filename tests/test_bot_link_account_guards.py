import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.link_account import LinkState, handle_address_completion, handle_contact


class FakeState:
    def __init__(self):
        self.cleared = 0
        self.data = {"telegram_link_token": "unit-token"}
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
    def __init__(self, user):
        self.user = user

    async def execute(self, stmt):
        return FakeExecuteResult(self.user)


def db_factory(user):
    async def _gen():
        yield FakeDB(user)
    return _gen


def make_message(contact_user_id=10, phone="+989121111111", from_user_id=10, username="u", full_name="User Name"):
    return SimpleNamespace(
        contact=SimpleNamespace(user_id=contact_user_id, phone_number=phone),
        from_user=SimpleNamespace(id=from_user_id, username=username, full_name=full_name),
        answer=AsyncMock(),
    )


class BotLinkAccountGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_contact_rejects_contact_for_other_sender(self):
        state = FakeState()
        message = make_message(contact_user_id=99, from_user_id=10)

        await handle_contact(message, state)

        self.assertIn("شماره خودتان", message.answer.await_args.args[0])
        self.assertEqual(state.cleared, 0)

    async def test_handle_contact_handles_missing_already_linked_and_already_connected_users(self):
        message = make_message()
        state = FakeState()
        with patch("bot.handlers.link_account.get_db", new=db_factory(None)), patch(
            "bot.handlers.link_account.is_user_accountant",
            new=AsyncMock(return_value=False),
        ):
            await handle_contact(message, state)
        self.assertIn("همگام‌سازی", message.answer.await_args.args[0])
        self.assertEqual(state.cleared, 1)

        other_user = SimpleNamespace(id=2, telegram_id=77, account_name="acc", full_name="acc", address="تهران خیابان آزادی پلاک ۱۰")
        message = make_message()
        state = FakeState()
        with patch("bot.handlers.link_account.get_db", new=db_factory(other_user)), patch(
            "bot.handlers.link_account.is_user_accountant",
            new=AsyncMock(return_value=False),
        ):
            await handle_contact(message, state)
        self.assertIn("قبلاً به یک اکانت تلگرام دیگر", message.answer.await_args.args[0])
        self.assertEqual(state.cleared, 1)

        same_user = SimpleNamespace(id=3, telegram_id=10, account_name="acc", full_name="acc", address="تهران خیابان آزادی پلاک ۱۰")
        message = make_message()
        state = FakeState()
        with patch("bot.handlers.link_account.get_db", new=db_factory(same_user)), patch(
            "bot.handlers.link_account.is_user_accountant",
            new=AsyncMock(return_value=False),
        ):
            await handle_contact(message, state)
        self.assertIn("قبلاً متصل شده", message.answer.await_args.args[0])
        self.assertEqual(state.cleared, 1)

    async def test_handle_contact_blocks_accountant_bot_linking(self):
        accountant_user = SimpleNamespace(id=5, telegram_id=None, account_name="acc", full_name="acc", address="تهران خیابان آزادی پلاک ۱۰")
        message = make_message()
        state = FakeState()

        with patch("bot.handlers.link_account.get_db", new=db_factory(accountant_user)), patch(
            "bot.handlers.link_account.is_user_accountant",
            new=AsyncMock(return_value=True),
        ):
            await handle_contact(message, state)

        self.assertIn("حسابدارها به ربات تلگرام دسترسی ندارند", message.answer.await_args.args[0])
        self.assertEqual(state.cleared, 1)

    async def test_handle_contact_blocks_customer_bot_linking(self):
        customer_user = SimpleNamespace(id=6, telegram_id=None, account_name="cust", full_name="cust", address="تهران خیابان آزادی پلاک ۱۰")
        message = make_message()
        state = FakeState()

        with patch("bot.handlers.link_account.get_db", new=db_factory(customer_user)), patch(
            "bot.handlers.link_account.is_user_accountant",
            new=AsyncMock(return_value=False),
        ), patch(
            "bot.handlers.link_account.is_user_customer",
            new=AsyncMock(return_value=True),
        ):
            await handle_contact(message, state)

        self.assertIn("دسترسی این سطح مشتری به ربات تلگرام فعال نیست", message.answer.await_args.args[0])
        self.assertEqual(state.cleared, 1)

    async def test_handle_address_completion_guard_branches(self):
        message = SimpleNamespace(text="short", answer=AsyncMock())
        state = FakeState()
        await handle_address_completion(message, state)
        self.assertIn("آدرس وارد شده کوتاه است", message.answer.await_args.args[0])

        message = SimpleNamespace(text="تهران خیابان آزادی پلاک ۱۰", answer=AsyncMock())
        state = FakeState()
        await handle_address_completion(message, state)
        self.assertEqual(state.cleared, 1)
        self.assertIn("فرآیند تکمیل ثبت‌نام منقضی شده", message.answer.await_args.args[0])

        state = FakeState()
        await state.update_data(link_user_id=9)
        message = SimpleNamespace(text="تهران خیابان آزادی پلاک ۱۰", answer=AsyncMock())
        with patch("bot.handlers.link_account.get_db", new=db_factory(None)):
            await handle_address_completion(message, state)
        self.assertEqual(state.cleared, 1)
        self.assertIn("همگام‌سازی", message.answer.await_args.args[0])

        accountant_user = SimpleNamespace(id=9, telegram_id=None, account_name="acc", full_name="acc", address="System Default")
        state = FakeState()
        await state.update_data(link_user_id=9)
        message = SimpleNamespace(text="تهران خیابان آزادی پلاک ۱۰", answer=AsyncMock(), from_user=SimpleNamespace(id=10))
        with patch("bot.handlers.link_account.get_db", new=db_factory(accountant_user)), patch(
            "bot.handlers.link_account.is_user_accountant", new=AsyncMock(return_value=True)
        ):
            await handle_address_completion(message, state)
        self.assertEqual(state.cleared, 1)
        self.assertIn("حسابدارها به ربات تلگرام دسترسی ندارند", message.answer.await_args.args[0])

        customer_user = SimpleNamespace(id=9, telegram_id=None, account_name="cust", full_name="cust", address="System Default")
        state = FakeState()
        await state.update_data(link_user_id=9)
        message = SimpleNamespace(text="تهران خیابان آزادی پلاک ۱۰", answer=AsyncMock(), from_user=SimpleNamespace(id=10))
        with patch("bot.handlers.link_account.get_db", new=db_factory(customer_user)), patch(
            "bot.handlers.link_account.is_user_accountant", new=AsyncMock(return_value=False)
        ), patch(
            "bot.handlers.link_account.is_user_customer", new=AsyncMock(return_value=True)
        ):
            await handle_address_completion(message, state)
        self.assertEqual(state.cleared, 1)
        self.assertIn("دسترسی این سطح مشتری به ربات تلگرام فعال نیست", message.answer.await_args.args[0])

        linked_elsewhere = SimpleNamespace(id=9, telegram_id=77, account_name="acc", full_name="acc", address="System Default")
        state = FakeState()
        await state.update_data(link_user_id=9)
        message = SimpleNamespace(text="تهران خیابان آزادی پلاک ۱۰", answer=AsyncMock(), from_user=SimpleNamespace(id=10))
        with patch("bot.handlers.link_account.get_db", new=db_factory(linked_elsewhere)), patch(
            "bot.handlers.link_account.is_user_accountant", new=AsyncMock(return_value=False)
        ):
            await handle_address_completion(message, state)
        self.assertEqual(state.cleared, 1)
        self.assertIn("قبلاً به یک اکانت تلگرام دیگر", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
