import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from models.customer_relation import CustomerRelationStatus, CustomerTier
from core.enums import UserRole
from bot.handlers import panel


class FakeSessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeScalarResult:
    def all(self):
        return []


class FakeExecuteResult:
    def scalars(self):
        return FakeScalarResult()


class CapturingSession:
    def __init__(self):
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return FakeExecuteResult()


class BotPanelStandardActionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_standard_user_panel_renders_action_menu_for_non_customer(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=10),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=77)),
        )
        user = SimpleNamespace(
            id=5,
            role=UserRole.STANDARD,
            account_status=None,
            messenger_blocked_at=None,
            messenger_grace_expires_at=None,
        )

        with patch("bot.handlers.panel.delete_previous_anchor", new=AsyncMock()), patch(
            "core.services.customer_relation_service.is_user_customer", new=AsyncMock(return_value=False)
        ), patch("bot.handlers.panel.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.panel.set_anchor"
        ) as set_anchor:
            await panel.show_my_profile_and_change_keyboard(message, state=SimpleNamespace(), user=user)

        self.assertIn("پنل کاربر", message.answer.await_args.args[0])
        self.assertNotIn("پروفایل شما", message.answer.await_args.args[0])
        markup = message.answer.await_args.kwargs["reply_markup"]
        texts = [button.text for row in markup.keyboard for button in row]
        self.assertIn("📄 معاملات اخیر", texts)
        self.assertIn("🚫 کاربران مسدود شده", texts)
        self.assertIn("👥 مشتریان", texts)
        set_anchor.assert_called_once_with(10, 77)

    async def test_recent_trades_pdf_uses_shared_export_service_and_cleans_temp_file(self):
        message = SimpleNamespace(answer=AsyncMock(), answer_document=AsyncMock())
        user = SimpleNamespace(id=9, account_name="dev")
        trade = SimpleNamespace(id=1)

        with patch("bot.handlers.panel._load_recent_user_trades", new=AsyncMock(return_value=[trade])), patch(
            "bot.handlers.panel.build_trade_history_export_rows", return_value=["row"]
        ) as rows_mock, patch(
            "bot.handlers.panel.generate_trade_history_pdf_file", return_value="/tmp/recent.pdf"
        ) as pdf_mock, patch("bot.handlers.panel.os.path.exists", return_value=True), patch(
            "bot.handlers.panel.os.remove"
        ) as remove_mock:
            await panel.show_recent_trades_pdf(message, state=SimpleNamespace(), user=user)

        rows_mock.assert_called_once_with([trade], 9)
        pdf_mock.assert_called_once()
        message.answer_document.assert_awaited_once()
        remove_mock.assert_called_once_with("/tmp/recent.pdf")

    async def test_recent_trades_pdf_reports_empty_history(self):
        message = SimpleNamespace(answer=AsyncMock(), answer_document=AsyncMock())
        with patch("bot.handlers.panel._load_recent_user_trades", new=AsyncMock(return_value=[])):
            await panel.show_recent_trades_pdf(message, state=SimpleNamespace(), user=SimpleNamespace(id=9))

        self.assertIn("هفت روز گذشته", message.answer.await_args.args[0])
        message.answer_document.assert_not_awaited()

    def test_blocked_and_customer_keyboards(self):
        blocked_keyboard = panel.get_user_panel_blocked_keyboard([{"id": 4, "account_name": "ali"}])
        self.assertIn("رفع مسدودیت", blocked_keyboard.inline_keyboard[0][0].text)

        relation = SimpleNamespace(
            id=3,
            management_name="مشتری تست",
            status=CustomerRelationStatus.ACTIVE,
            customer_tier=CustomerTier.TIER_1,
            customer_user=SimpleNamespace(account_name="customer_3"),
        )
        tier_2_relation = SimpleNamespace(
            id=4,
            management_name="مشتری دوم",
            status=CustomerRelationStatus.PENDING,
            customer_tier=CustomerTier.TIER_2,
            customer_user=SimpleNamespace(account_name="customer_4"),
        )
        customers_keyboard = panel.get_user_panel_customers_keyboard([relation, tier_2_relation])
        button_texts = [button.text for row in customers_keyboard.inline_keyboard for button in row]
        self.assertIn("➕ دعوت مشتری", button_texts)
        self.assertIn("👤 مشتری تست | سطح ۱ | فعال", button_texts)
        self.assertIn("👤 مشتری دوم | سطح ۲ | در انتظار ثبت‌نام", button_texts)

        detail_keyboard = panel.get_customer_detail_keyboard(relation)
        self.assertIn("اخراج مشتری", detail_keyboard.inline_keyboard[0][0].text)

    async def test_customer_invite_placeholder_only_answers(self):
        callback = SimpleNamespace(answer=AsyncMock())
        await panel.user_panel_customer_invite_placeholder(callback, user=SimpleNamespace(id=1))
        callback.answer.assert_awaited_once_with("دعوت مشتری از بات در مرحله بعد اضافه می‌شود.", show_alert=True)

    async def test_colleagues_list_shows_non_relation_users(self):
        message = SimpleNamespace(answer=AsyncMock())
        user = SimpleNamespace(
            id=5,
            role=UserRole.STANDARD,
            account_status=None,
            messenger_blocked_at=None,
            messenger_grace_expires_at=None,
        )
        colleagues = [
            SimpleNamespace(id=7, account_name="ali", full_name="Ali"),
            SimpleNamespace(id=8, account_name="reza", full_name="Reza"),
        ]

        with patch("bot.handlers.panel.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.panel._can_view_colleagues_list", new=AsyncMock(return_value=True)
        ), patch("bot.handlers.panel._load_colleagues_for_user", new=AsyncMock(return_value=colleagues)):
            await panel.show_colleagues_list(message, state=SimpleNamespace(), user=user)

        self.assertIn("لیست همکاران", message.answer.await_args.args[0])
        self.assertIn("ali", message.answer.await_args.args[0])
        self.assertIn("reza", message.answer.await_args.args[0])

    async def test_colleagues_list_rejects_non_standard_relation_contexts(self):
        message = SimpleNamespace(answer=AsyncMock())
        user = SimpleNamespace(
            id=5,
            role=UserRole.STANDARD,
            account_status=None,
            messenger_blocked_at=None,
            messenger_grace_expires_at=None,
        )

        with patch("bot.handlers.panel.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.panel._can_view_colleagues_list", new=AsyncMock(return_value=False)
        ), patch("bot.handlers.panel._load_colleagues_for_user", new=AsyncMock()) as load_mock:
            await panel.show_colleagues_list(message, state=SimpleNamespace(), user=user)

        self.assertIn("کاربران عادی", message.answer.await_args.args[0])
        load_mock.assert_not_awaited()

    async def test_colleagues_query_excludes_any_non_deleted_customer_or_accountant_relation(self):
        session = CapturingSession()
        await panel._load_colleagues_for_user(session, user_id=5)

        compiled = str(session.statement.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("customer_relations.deleted_at IS NULL", compiled)
        self.assertIn("accountant_relations.deleted_at IS NULL", compiled)
        self.assertNotIn("customer_relations.status", compiled)
        self.assertNotIn("accountant_relations.status", compiled)


if __name__ == "__main__":
    unittest.main()
