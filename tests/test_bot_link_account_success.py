import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.link_account import LinkState, handle_address_completion, handle_contact
from core.enums import UserAccountStatus, UserRole


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


def make_user(**overrides):
    data = {
        "id": 99,
        "telegram_id": None,
        "username": None,
        "full_name": "acc",
        "account_name": "acc",
        "mobile_number": "09121111111",
        "has_bot_access": False,
        "address": "تهران خیابان آزادی پلاک ۱۰",
        "role": UserRole.STANDARD,
        "account_status": UserAccountStatus.ACTIVE,
        "is_deleted": False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class BotLinkAccountSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_contact_links_account_and_normalizes_phone(self):
        user = make_user()
        db = FakeDB(user)
        state = FakeState()
        message = make_message(username="mohsen_telegram", full_name="Linked_User")

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ) as mandatory_mock, patch(
            "bot.handlers.link_account.build_channel_join_request_text",
            new=AsyncMock(return_value="🔗 درخواست عضویت در کانال معاملات:\nhttps://t.me/+unit_token"),
        ), patch(
            "bot.handlers.link_account.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ), patch(
            "bot.handlers.link_account.get_persistent_menu_keyboard",
            return_value="menu",
        ):
            await handle_contact(message, state)

        self.assertEqual(user.telegram_id, 10)
        self.assertEqual(user.username, "mohsen_telegram")
        self.assertEqual(user.full_name, "Linked_User")
        self.assertTrue(user.has_bot_access)
        self.assertIs(mandatory_mock.await_args.kwargs["user"], user)
        self.assertEqual(db.commits, 1)
        self.assertEqual(state.cleared, 1)
        answer_text = message.answer.await_args.args[0]
        self.assertIn("با موفقیت", answer_text)
        self.assertIn("https://t.me/+unit_token", answer_text)
        self.assertIn("https://app.example", answer_text)
        self.assertIsNone(message.answer.await_args.kwargs.get("parse_mode"))
        self.assertEqual(message.answer.await_args.kwargs["reply_markup"], "menu")

    async def test_handle_contact_rolls_back_and_reports_commit_error(self):
        user = make_user()
        db = FakeDB(user, commit_error=RuntimeError("db down"))
        state = FakeState()
        message = make_message()

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.link_account.build_channel_join_request_text",
            new=AsyncMock(return_value=None),
        ), patch(
            "bot.handlers.link_account.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ):
            await handle_contact(message, state)

        self.assertEqual(db.rollbacks, 1)
        self.assertEqual(state.cleared, 1)
        self.assertIn("خطا در اتصال حساب", message.answer.await_args.args[0])

    async def test_handle_contact_reports_sync_pending_when_account_row_is_missing(self):
        db = FakeDB(None)
        state = FakeState()
        message = make_message()

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ):
            await handle_contact(message, state)

        self.assertEqual(db.commits, 0)
        self.assertEqual(state.cleared, 1)
        self.assertIn("همگام‌سازی", message.answer.await_args.args[0])
        self.assertIn("دوباره تلاش کنید", message.answer.await_args.args[0])

    async def test_handle_contact_denies_inactive_or_deleted_accounts(self):
        inactive_user = SimpleNamespace(
            telegram_id=None,
            username=None,
            full_name="acc",
            account_name="acc",
            has_bot_access=False,
            address="تهران خیابان آزادی پلاک ۱۰",
            is_deleted=False,
            account_status=UserAccountStatus.INACTIVE,
            role=UserRole.STANDARD,
        )
        db = FakeDB(inactive_user)
        state = FakeState()
        message = make_message()

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ):
            await handle_contact(message, state)

        self.assertEqual(db.commits, 0)
        self.assertEqual(state.cleared, 1)
        self.assertIn("غیرفعال", message.answer.await_args.args[0])

        deleted_user = SimpleNamespace(
            telegram_id=None,
            username=None,
            full_name="acc",
            account_name="acc",
            has_bot_access=False,
            address="تهران خیابان آزادی پلاک ۱۰",
            is_deleted=True,
            account_status=UserAccountStatus.ACTIVE,
            role=UserRole.STANDARD,
        )
        db = FakeDB(deleted_user)
        state = FakeState()
        message = make_message()

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)):
            await handle_contact(message, state)

        self.assertEqual(db.commits, 0)
        self.assertEqual(state.cleared, 1)
        self.assertIn("در دسترس نیست", message.answer.await_args.args[0])

    async def test_handle_contact_for_placeholder_address_prompts_for_address_completion(self):
        user = make_user(address="System Default")
        db = FakeDB(user)
        state = FakeState()
        message = make_message()

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ):
            await handle_contact(message, state)

        self.assertEqual(user.telegram_id, 10)
        self.assertEqual(db.commits, 1)
        self.assertEqual(state.data["link_user_id"], 99)
        self.assertEqual(state.states, [LinkState.waiting_for_address])
        self.assertIn("تکمیل ثبت‌نام", message.answer.await_args.args[0])

    async def test_handle_contact_and_address_completion_cover_permission_and_error_paths(self):
        user = make_user()
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
        self.assertIn("دسترسی این سطح مشتری به ربات تلگرام فعال نیست", message.answer.await_args.args[0])

        user = make_user(address="System Default")
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
        user = make_user(address="System Default")
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
            "bot.handlers.link_account.build_channel_join_request_text",
            new=AsyncMock(return_value="🔗 درخواست عضویت در کانال معاملات:\nhttps://t.me/joinreq"),
        ), patch(
            "bot.handlers.link_account.settings",
            SimpleNamespace(frontend_url="https://app.example"),
        ), patch(
            "bot.handlers.link_account.get_persistent_menu_keyboard",
            return_value="menu",
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
        self.assertIsNone(message.answer.await_args.kwargs.get("parse_mode"))
        self.assertEqual(message.answer.await_args.kwargs["reply_markup"], "menu")

    async def test_legacy_address_completion_keeps_deployed_trim_behavior(self):
        user = make_user(address="System Default")
        db = FakeDB(user)
        state = FakeState()
        await state.update_data(link_user_id=99)
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            text="  تهران خیابان آزادی پلاک ۱۰  ",
            from_user=SimpleNamespace(id=10, username="u", full_name="Linked User"),
            answer=AsyncMock(),
        )

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.link_account.build_linked_account_panel_message",
            new=AsyncMock(return_value="linked"),
        ), patch(
            "bot.handlers.link_account.settings",
            SimpleNamespace(
                frontend_url="https://app.example",
                registration_sync_v2_enabled=False,
            ),
        ):
            await handle_address_completion(message, state)

        self.assertEqual(user.address, "تهران خیابان آزادی پلاک ۱۰")

    async def test_sync_v2_contact_forwards_without_foreign_user_mutation(self):
        user = make_user()
        projected_user = make_user(telegram_id=10, has_bot_access=True)
        db = FakeDB(user)
        state = FakeState()
        message = make_message(username="mohsen_telegram", full_name="Linked User")

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.settings",
            SimpleNamespace(
                frontend_url="https://foreign.invalid",
                registration_sync_v2_enabled=True,
            ),
        ), patch(
            "bot.handlers.link_account.complete_account_link_via_iran",
            new=AsyncMock(return_value=projected_user),
        ) as complete, patch(
            "bot.handlers.link_account.build_linked_account_panel_message",
            new=AsyncMock(return_value="linked"),
        ), patch(
            "bot.handlers.link_account.public_webapp_url_for_links",
            return_value="https://iran.example",
        ), patch(
            "bot.handlers.link_account.get_persistent_menu_keyboard",
            return_value="menu",
        ):
            await handle_contact(message, state)

        self.assertIsNone(user.telegram_id)
        self.assertFalse(user.has_bot_access)
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)
        complete.assert_awaited_once_with(
            message=message,
            mobile_number="09121111111",
            link_token="unit-token",
            address=None,
        )
        self.assertEqual(state.cleared, 1)
        self.assertEqual(message.answer.await_args.kwargs["reply_markup"], "menu")

    async def test_sync_v2_contact_defers_incomplete_address_without_consuming_token(self):
        user = make_user(address="System Default")
        db = FakeDB(user)
        state = FakeState()
        message = make_message()

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.settings",
            SimpleNamespace(registration_sync_v2_enabled=True),
        ), patch(
            "bot.handlers.link_account.finalize_account_link",
            new=AsyncMock(),
        ) as legacy_finalize, patch(
            "bot.handlers.link_account.complete_account_link_via_iran",
            new=AsyncMock(),
        ) as complete:
            await handle_contact(message, state)

        self.assertIsNone(user.telegram_id)
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)
        self.assertEqual(state.data["telegram_link_token"], "unit-token")
        self.assertEqual(state.data["telegram_link_mobile"], "09121111111")
        self.assertEqual(state.states, [LinkState.waiting_for_address])
        legacy_finalize.assert_not_awaited()
        complete.assert_not_awaited()

    async def test_sync_v2_address_is_forwarded_exactly_and_not_written_on_foreign(self):
        user = make_user(address="System Default")
        projected_user = make_user(
            telegram_id=10,
            address="  تهران خیابان آزادی پلاک ۱۰  ",
            has_bot_access=True,
        )
        db = FakeDB(user)
        state = FakeState()
        await state.update_data(
            link_user_id=99,
            telegram_link_mobile="09121111111",
        )
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            text="  تهران خیابان آزادی پلاک ۱۰  ",
            from_user=SimpleNamespace(id=10, username="u", full_name="Linked User"),
            answer=AsyncMock(),
        )

        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.settings",
            SimpleNamespace(registration_sync_v2_enabled=True),
        ), patch(
            "bot.handlers.link_account.complete_account_link_via_iran",
            new=AsyncMock(return_value=projected_user),
        ) as complete, patch(
            "bot.handlers.link_account.build_linked_account_panel_message",
            new=AsyncMock(return_value="linked"),
        ), patch(
            "bot.handlers.link_account.public_webapp_url_for_links",
            return_value="https://iran.example",
        ), patch(
            "bot.handlers.link_account.get_persistent_menu_keyboard",
            return_value="menu",
        ):
            await handle_address_completion(message, state)

        self.assertEqual(user.address, "System Default")
        self.assertIsNone(user.telegram_id)
        self.assertEqual(db.commits, 0)
        self.assertEqual(db.rollbacks, 1)
        complete.assert_awaited_once_with(
            message=message,
            mobile_number="09121111111",
            link_token="unit-token",
            address="  تهران خیابان آزادی پلاک ۱۰  ",
        )
        self.assertEqual(state.cleared, 1)


if __name__ == "__main__":
    unittest.main()
