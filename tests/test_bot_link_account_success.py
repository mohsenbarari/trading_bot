import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.link_account import handle_contact


class FakeState:
    def __init__(self):
        self.cleared = 0

    async def clear(self):
        self.cleared += 1


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
        user = SimpleNamespace(telegram_id=None, username=None, full_name="acc", account_name="acc", has_bot_access=False)
        db = FakeDB(user)
        state = FakeState()
        message = make_message()

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)):
            await handle_contact(message, state)

        self.assertEqual(user.telegram_id, 10)
        self.assertEqual(user.username, "u")
        self.assertEqual(user.full_name, "Linked User")
        self.assertTrue(user.has_bot_access)
        self.assertEqual(db.commits, 1)
        self.assertEqual(state.cleared, 1)
        self.assertIn("با موفقیت", message.answer.await_args.args[0])

    async def test_handle_contact_rolls_back_and_reports_commit_error(self):
        user = SimpleNamespace(telegram_id=None, username=None, full_name="acc", account_name="acc", has_bot_access=False)
        db = FakeDB(user, commit_error=RuntimeError("db down"))
        state = FakeState()
        message = make_message()

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)):
            await handle_contact(message, state)

        self.assertEqual(db.rollbacks, 1)
        self.assertEqual(state.cleared, 1)
        self.assertIn("خطا در اتصال حساب", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()