import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.link_account import LinkState, handle_address_completion, handle_contact


class FakeState:
    def __init__(self):
        self.cleared = 0
        self.data = {}
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
    def __init__(self, user, commit_error=None):
        self.user = user
        self.commit_error = commit_error
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt):
        return FakeExecuteResult(self.user)

    async def commit(self):
        self.commits += 1
        if self.commit_error:
            raise self.commit_error

    async def rollback(self):
        self.rollbacks += 1


def db_factory(db):
    async def _gen():
        yield db
    return _gen


def make_message(phone="+989121111111", from_user_id=10, username="u", full_name="Linked User"):
    return SimpleNamespace(
        contact=SimpleNamespace(user_id=from_user_id, phone_number=phone),
        from_user=SimpleNamespace(id=from_user_id, username=username, full_name=full_name),
        answer=AsyncMock(),
    )


class BotLinkAccountSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_contact_links_account_and_normalizes_phone(self):
        user = SimpleNamespace(telegram_id=None, username=None, full_name="acc", account_name="acc", has_bot_access=False, address="تهران خیابان آزادی پلاک ۱۰")
        db = FakeDB(user)
        state = FakeState()
        message = make_message()

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ) as mandatory_mock, patch(
            "bot.handlers.link_account.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ):
            await handle_contact(message, state)

        self.assertEqual(user.telegram_id, 10)
        self.assertEqual(user.username, "u")
        self.assertEqual(user.full_name, "Linked User")
        self.assertTrue(user.has_bot_access)
        self.assertIs(mandatory_mock.await_args.kwargs["user"], user)
        self.assertEqual(db.commits, 1)
        self.assertEqual(state.cleared, 1)
        self.assertIn("با موفقیت", message.answer.await_args.args[0])
        self.assertIn("ورود به وب اپ", message.answer.await_args.args[0])

    async def test_handle_contact_rolls_back_and_reports_commit_error(self):
        user = SimpleNamespace(telegram_id=None, username=None, full_name="acc", account_name="acc", has_bot_access=False, address="تهران خیابان آزادی پلاک ۱۰")
        db = FakeDB(user, commit_error=RuntimeError("db down"))
        state = FakeState()
        message = make_message()

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.link_account.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ):
            await handle_contact(message, state)

        self.assertEqual(db.rollbacks, 1)
        self.assertEqual(state.cleared, 1)
        self.assertIn("خطا در اتصال حساب", message.answer.await_args.args[0])

    async def test_handle_contact_for_placeholder_address_prompts_for_address_completion(self):
        user = SimpleNamespace(
            id=99,
            telegram_id=None,
            username=None,
            full_name="acc",
            account_name="acc",
            has_bot_access=False,
            address="System Default",
        )
        db = FakeDB(user)
        state = FakeState()
        message = make_message()

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)):
            await handle_contact(message, state)

        self.assertIsNone(user.telegram_id)
        self.assertEqual(db.commits, 0)
        self.assertEqual(state.data["link_user_id"], 99)
        self.assertEqual(state.states, [LinkState.waiting_for_address])
        self.assertIn("تکمیل ثبت‌نام", message.answer.await_args.args[0])

    async def test_handle_contact_and_address_completion_cover_permission_and_error_paths(self):
        user = SimpleNamespace(telegram_id=None, username=None, full_name="acc", account_name="acc", has_bot_access=False, address="تهران خیابان آزادی پلاک ۱۰")
        db = FakeDB(user)
        state = FakeState()
        message = make_message()

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.finalize_account_link", new=AsyncMock(side_effect=PermissionError("ACCOUNTANT_BOT_ACCESS_FORBIDDEN"))
        ):
            await handle_contact(message, state)
        self.assertEqual(state.cleared, 1)
        self.assertIn("حسابدارها به ربات تلگرام دسترسی ندارند", message.answer.await_args.args[0])

        state = FakeState()
        message = make_message()
        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.finalize_account_link", new=AsyncMock(side_effect=PermissionError("CUSTOMER_BOT_ACCESS_FORBIDDEN"))
        ):
            await handle_contact(message, state)
        self.assertEqual(state.cleared, 1)
        self.assertIn("مشتری‌ها در این فاز به ربات تلگرام دسترسی ندارند", message.answer.await_args.args[0])

        user = SimpleNamespace(
            id=99,
            telegram_id=None,
            username=None,
            full_name="acc",
            account_name="acc",
            has_bot_access=False,
            address="System Default",
        )
        db = FakeDB(user)
        state = FakeState()
        await state.update_data(link_user_id=99)
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            text="تهران خیابان آزادی پلاک ۱۰",
            from_user=SimpleNamespace(id=10, username="u", full_name="Linked User"),
            answer=AsyncMock(),
        )
        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.finalize_account_link", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            await handle_address_completion(message, state)
        self.assertEqual(db.rollbacks, 1)
        self.assertEqual(state.cleared, 1)
        self.assertIn("خطا در تکمیل ثبت‌نام", message.answer.await_args.args[0])

    async def test_handle_address_completion_links_user_and_saves_address(self):
        user = SimpleNamespace(
            id=99,
            telegram_id=None,
            username=None,
            full_name="acc",
            account_name="acc",
            has_bot_access=False,
            address="System Default",
        )
        db = FakeDB(user)
        state = FakeState()
        await state.update_data(link_user_id=99)
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            text="تهران خیابان آزادی پلاک ۱۰",
            from_user=SimpleNamespace(id=10, username="u", full_name="Linked User"),
            answer=AsyncMock(),
        )

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ) as mandatory_mock, patch(
            "bot.handlers.link_account.build_channel_join_request_line",
            new=AsyncMock(return_value="🔗 [درخواست عضویت در کانال معاملات](https://t.me/joinreq)"),
        ), patch(
            "bot.handlers.link_account.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ):
            await handle_address_completion(message, state)

        self.assertEqual(user.telegram_id, 10)
        self.assertEqual(user.username, "u")
        self.assertEqual(user.address, "تهران خیابان آزادی پلاک ۱۰")
        self.assertTrue(user.has_bot_access)
        self.assertIs(mandatory_mock.await_args.kwargs["user"], user)
        self.assertEqual(db.commits, 1)
        self.assertEqual(state.cleared, 1)
        self.assertIn("درخواست عضویت در کانال معاملات", message.answer.await_args.args[0])
        self.assertIn("ورود به وب اپ", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()