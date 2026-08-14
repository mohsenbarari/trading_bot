import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from bot.handlers import link_account
from bot.handlers.link_account import (
    BotAccountLinkDenied,
    BotAccountLinkPending,
    LinkState,
    complete_account_link_via_iran,
    finalize_account_link,
    handle_address_completion,
    handle_contact,
)
from core.registration_contracts import TelegramRegistrationOutcome
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
    async def test_welcome_panel_prefers_project_account_name_over_telegram_full_name(self):
        user = make_user(account_name="final_test", full_name="Salar")

        with patch(
            "bot.handlers.link_account.attach_customer_management_names",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.link_account.build_channel_join_request_text",
            new=AsyncMock(return_value=None),
        ), patch(
            "bot.handlers.link_account.build_webapp_plain_link_line",
            return_value=None,
        ):
            text = await link_account.build_linked_account_panel_message(None, user, db=object())

        self.assertIn("سلام final_test!", text)
        self.assertNotIn("Salar", text)

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
            SimpleNamespace(
                iran_server_url="https://coin.gold-trade.ir/",
                frontend_url="https://coin.362514.ir",
            ),
        ), patch(
            "bot.handlers.link_account.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="menu"),
        ) as keyboard_mock:
            await handle_contact(message, state)

        self.assertEqual(user.telegram_id, 10)
        self.assertEqual(user.username, "mohsen_telegram")
        self.assertEqual(user.full_name, "acc")
        self.assertTrue(user.has_bot_access)
        self.assertIs(mandatory_mock.await_args.kwargs["user"], user)
        self.assertEqual(db.commits, 1)
        self.assertEqual(state.cleared, 1)
        answer_text = message.answer.await_args.args[0]
        self.assertIn("با موفقیت", answer_text)
        self.assertIn("https://t.me/+unit_token", answer_text)
        self.assertIn("https://coin.gold-trade.ir", answer_text)
        self.assertNotIn("coin.362514.ir", answer_text)
        self.assertIsNone(message.answer.await_args.kwargs.get("parse_mode"))
        self.assertEqual(message.answer.await_args.kwargs["reply_markup"], "menu")
        keyboard_mock.assert_awaited_once_with(user, "https://coin.gold-trade.ir")

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
            "bot.handlers.link_account.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="menu"),
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
            "bot.handlers.link_account.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="menu"),
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
            "bot.handlers.link_account.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="menu"),
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
            "bot.handlers.link_account.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="menu"),
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

    async def test_finalize_account_link_rejects_policy_denials(self):
        message = make_message()
        user = make_user()
        db = FakeDB(user)
        with patch.object(
            link_account.settings, "registration_sync_v2_enabled", False
        ), patch.object(
            link_account,
            "bot_account_access_denial_reason",
            return_value="account_inactive",
        ), self.assertRaises(BotAccountLinkDenied):
            await finalize_account_link(db, user, message)

        for reason, expected in (
            ("accountant", "ACCOUNTANT_BOT_ACCESS_FORBIDDEN"),
            ("customer", "CUSTOMER_BOT_ACCESS_FORBIDDEN"),
        ):
            with self.subTest(reason=reason), patch.object(
                link_account.settings, "registration_sync_v2_enabled", False
            ), patch.object(
                link_account, "bot_account_access_denial_reason", return_value=None
            ), patch.object(
                link_account,
                "get_web_only_bot_access_reason",
                new=AsyncMock(return_value=reason),
            ), self.assertRaisesRegex(PermissionError, expected):
                await finalize_account_link(db, user, message)

        successful = make_user(full_name="Display Name", account_name="account")
        with patch.object(
            link_account.settings, "registration_sync_v2_enabled", False
        ), patch.object(
            link_account, "bot_account_access_denial_reason", return_value=None
        ), patch.object(
            link_account,
            "get_web_only_bot_access_reason",
            new=AsyncMock(return_value=None),
        ), patch.object(
            link_account,
            "consume_telegram_link_token",
            new=AsyncMock(),
        ) as consume, patch.object(
            link_account,
            "ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ):
            await finalize_account_link(
                db,
                successful,
                message,
                token_record=SimpleNamespace(),
                send_success_message=False,
            )
        consume.assert_awaited_once()

        no_token = make_user(full_name="Existing Name", account_name="account")
        with patch.object(
            link_account.settings, "registration_sync_v2_enabled", False
        ), patch.object(
            link_account, "bot_account_access_denial_reason", return_value=None
        ), patch.object(
            link_account,
            "get_web_only_bot_access_reason",
            new=AsyncMock(return_value=None),
        ), patch.object(
            link_account,
            "ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ):
            await finalize_account_link(
                db, no_token, message, send_success_message=False
            )

    async def test_projection_wait_success_and_timeout(self):
        user = make_user(telegram_id=10)
        db = FakeDB(user)

        class SessionContext:
            async def __aenter__(self):
                return db

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with patch.object(link_account, "AsyncSessionLocal", return_value=SessionContext()), patch.object(
            link_account,
            "evaluate_bot_access",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True)),
        ):
            self.assertIs(
                await link_account._wait_for_linked_account_projection(
                    mobile_number=user.mobile_number,
                    telegram_id=10,
                    timeout_seconds=0,
                ),
                user,
            )

        db.user = None
        with patch.object(link_account, "AsyncSessionLocal", return_value=SessionContext()):
            self.assertIsNone(
                await link_account._wait_for_linked_account_projection(
                    mobile_number=user.mobile_number,
                    telegram_id=10,
                    timeout_seconds=0,
                )
            )

        loop = SimpleNamespace(time=MagicMock(side_effect=[0.0, 0.0, 2.0]))
        with patch.object(link_account, "AsyncSessionLocal", return_value=SessionContext()), patch.object(
            link_account.asyncio, "get_running_loop", return_value=loop
        ), patch.object(link_account.asyncio, "sleep", new=AsyncMock()) as sleep:
            self.assertIsNone(
                await link_account._wait_for_linked_account_projection(
                    mobile_number=user.mobile_number,
                    telegram_id=10,
                    timeout_seconds=1,
                )
            )
        sleep.assert_awaited_once()

    async def test_complete_via_iran_response_failure_matrix(self):
        message = make_message()
        command_id = uuid4()
        command = SimpleNamespace(
            command_id=command_id,
            mobile_number="09121111111",
            telegram_id=10,
        )
        cases = (
            ({"invalid": True}, 200, BotAccountLinkPending, "invalid_response"),
            (
                {
                    "command_id": str(command_id),
                    "outcome": TelegramRegistrationOutcome.LINKED_EXISTING.value,
                    "terminal": False,
                },
                200,
                BotAccountLinkPending,
                TelegramRegistrationOutcome.LINKED_EXISTING.value,
            ),
            (
                {
                    "command_id": str(uuid4()),
                    "outcome": TelegramRegistrationOutcome.LINKED_EXISTING.value,
                    "terminal": True,
                },
                200,
                BotAccountLinkPending,
                "response_command_mismatch",
            ),
            (
                {
                    "command_id": str(command_id),
                    "outcome": TelegramRegistrationOutcome.IDENTITY_CONFLICT.value,
                    "terminal": True,
                },
                400,
                BotAccountLinkDenied,
                TelegramRegistrationOutcome.IDENTITY_CONFLICT.value,
            ),
        )
        for body, status_code, error_type, detail in cases:
            with self.subTest(detail=detail), patch.object(
                link_account,
                "build_telegram_account_link_command",
                return_value=command,
            ), patch.object(
                link_account,
                "forward_telegram_account_link_command",
                new=AsyncMock(return_value=(status_code, body)),
            ), self.assertRaisesRegex(error_type, detail):
                await complete_account_link_via_iran(
                    message=message,
                    mobile_number="09121111111",
                    link_token="token",
                    address=None,
                )

        success_body = {
            "command_id": str(command_id),
            "outcome": TelegramRegistrationOutcome.LINKED_EXISTING.value,
            "terminal": True,
        }
        with patch.object(
            link_account,
            "build_telegram_account_link_command",
            return_value=command,
        ), patch.object(
            link_account,
            "forward_telegram_account_link_command",
            new=AsyncMock(return_value=(200, success_body)),
        ), patch.object(
            link_account,
            "_wait_for_linked_account_projection",
            new=AsyncMock(return_value=None),
        ), self.assertRaisesRegex(BotAccountLinkPending, "projection_pending"):
            await complete_account_link_via_iran(
                message=message,
                mobile_number="09121111111",
                link_token="token",
                address=None,
            )

    async def test_sync_v2_contact_and_address_forwarding_errors_are_user_safe(self):
        user = make_user()
        for failure in (
            BotAccountLinkPending("pending"),
            BotAccountLinkDenied("identity_conflict"),
            RuntimeError("unexpected"),
        ):
            db = FakeDB(user)
            state = FakeState()
            message = make_message()
            with self.subTest(surface="contact", failure=type(failure).__name__), patch(
                "bot.handlers.link_account.get_db", new=db_factory(db)
            ), patch(
                "bot.handlers.link_account.settings",
                SimpleNamespace(registration_sync_v2_enabled=True),
            ), patch.object(
                link_account,
                "complete_account_link_via_iran",
                new=AsyncMock(side_effect=failure),
            ), patch.object(
                link_account, "bot_account_access_denial_reason", return_value=None
            ), patch.object(
                link_account,
                "get_web_only_bot_access_reason",
                new=AsyncMock(return_value=None),
            ):
                await handle_contact(message, state)
            self.assertEqual(state.cleared, 1)

        for failure in (
            BotAccountLinkPending("pending"),
            BotAccountLinkDenied("identity_conflict"),
            RuntimeError("unexpected"),
        ):
            db = FakeDB(make_user(address="System Default"))
            state = FakeState()
            await state.update_data(
                link_user_id=99,
                telegram_link_mobile="09121111111",
            )
            message = SimpleNamespace(
                bot=SimpleNamespace(),
                text="تهران خیابان آزادی پلاک ۱۰",
                from_user=SimpleNamespace(id=10, username="u", full_name="User"),
                answer=AsyncMock(),
            )
            with self.subTest(surface="address", failure=type(failure).__name__), patch(
                "bot.handlers.link_account.get_db", new=db_factory(db)
            ), patch(
                "bot.handlers.link_account.settings",
                SimpleNamespace(registration_sync_v2_enabled=True),
            ), patch.object(
                link_account,
                "complete_account_link_via_iran",
                new=AsyncMock(side_effect=failure),
            ), patch.object(
                link_account, "bot_account_access_denial_reason", return_value=None
            ), patch.object(
                link_account,
                "get_web_only_bot_access_reason",
                new=AsyncMock(return_value=None),
            ):
                await handle_address_completion(message, state)
            self.assertEqual(state.cleared, 1)

    async def test_address_identity_guards_cover_falsy_and_absent_links(self):
        message = SimpleNamespace(
            text="تهران خیابان آزادی پلاک ۱۰",
            from_user=SimpleNamespace(id=10),
            answer=AsyncMock(),
        )
        for telegram_id, token in ((0, "unit-token"), (None, None)):
            user = make_user(telegram_id=telegram_id, address="System Default")
            db = FakeDB(user)
            state = FakeState()
            state.data = {"link_user_id": user.id}
            if token:
                state.data["telegram_link_token"] = token
            with self.subTest(telegram_id=telegram_id, token=token), patch(
                "bot.handlers.link_account.get_db", new=db_factory(db)
            ), patch.object(
                link_account.settings, "registration_sync_v2_enabled", True
            ), patch.object(
                link_account, "bot_account_access_denial_reason", return_value=None
            ), patch.object(
                link_account,
                "get_web_only_bot_access_reason",
                new=AsyncMock(return_value=None),
            ):
                await handle_address_completion(message, state)
            self.assertEqual(state.cleared, 1)

    async def test_legacy_contact_and_address_policy_errors_are_isolated(self):
        incomplete = make_user(address="System Default")
        failures = (
            BotAccountLinkDenied("account_inactive"),
            PermissionError("ACCOUNTANT_BOT_ACCESS_FORBIDDEN"),
            RuntimeError("unexpected"),
        )
        for failure in failures:
            db = FakeDB(incomplete)
            state = FakeState()
            message = make_message()
            with self.subTest(path="before_address", failure=type(failure).__name__), patch(
                "bot.handlers.link_account.get_db", new=db_factory(db)
            ), patch(
                "bot.handlers.link_account.settings",
                SimpleNamespace(registration_sync_v2_enabled=False),
            ), patch.object(
                link_account, "finalize_account_link", new=AsyncMock(side_effect=failure)
            ), patch.object(
                link_account, "bot_account_access_denial_reason", return_value=None
            ), patch.object(
                link_account,
                "get_web_only_bot_access_reason",
                new=AsyncMock(return_value=None),
            ):
                await handle_contact(message, state)
            self.assertEqual(state.cleared, 1)
            self.assertEqual(db.rollbacks, 1)

        complete = make_user(address="تهران خیابان آزادی پلاک ۱۰")
        db = FakeDB(complete)
        state = FakeState()
        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch(
            "bot.handlers.link_account.settings",
            SimpleNamespace(registration_sync_v2_enabled=False),
        ), patch.object(
            link_account,
            "finalize_account_link",
            new=AsyncMock(side_effect=BotAccountLinkDenied("account_inactive")),
        ), patch.object(
            link_account, "bot_account_access_denial_reason", return_value=None
        ), patch.object(
            link_account,
            "get_web_only_bot_access_reason",
            new=AsyncMock(return_value=None),
        ):
            await handle_contact(make_message(), state)
        self.assertEqual(db.rollbacks, 1)

        for failure in (
            BotAccountLinkDenied("account_inactive"),
            PermissionError("CUSTOMER_BOT_ACCESS_FORBIDDEN"),
        ):
            user = make_user(telegram_id=10, address="System Default")
            db = FakeDB(user)
            state = FakeState()
            state.data = {"link_user_id": user.id}
            message = SimpleNamespace(
                text="تهران خیابان آزادی پلاک ۱۰",
                from_user=SimpleNamespace(id=10),
                answer=AsyncMock(),
            )
            with self.subTest(path="address", failure=type(failure).__name__), patch(
                "bot.handlers.link_account.get_db", new=db_factory(db)
            ), patch(
                "bot.handlers.link_account.settings",
                SimpleNamespace(registration_sync_v2_enabled=False),
            ), patch.object(
                link_account, "finalize_account_link", new=AsyncMock(side_effect=failure)
            ), patch.object(
                link_account, "bot_account_access_denial_reason", return_value=None
            ), patch.object(
                link_account,
                "get_web_only_bot_access_reason",
                new=AsyncMock(return_value=None),
            ):
                await handle_address_completion(message, state)
            self.assertEqual(db.rollbacks, 1)

    async def test_contact_same_linked_user_with_incomplete_address_prompts(self):
        user = make_user(telegram_id=10, address="System Default")
        db = FakeDB(user)
        state = FakeState()
        with patch("bot.handlers.link_account.get_db", new=db_factory(db)), patch.object(
            link_account, "bot_account_access_denial_reason", return_value=None
        ), patch.object(
            link_account,
            "get_web_only_bot_access_reason",
            new=AsyncMock(return_value=None),
        ), patch.object(
            link_account,
            "prompt_address_completion",
            new=AsyncMock(),
        ) as prompt:
            await handle_contact(make_message(), state)
        prompt.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
