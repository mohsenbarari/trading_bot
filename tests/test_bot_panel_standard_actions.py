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


if __name__ == "__main__":
    unittest.main()
