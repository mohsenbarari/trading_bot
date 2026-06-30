import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from models.customer_relation import CustomerRelationStatus, CustomerTier
from core.enums import UserRole
from core.services.block_service import BLOCK_STATUS_REASON_ACCOUNTANT_DELEGATED
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


class FakeState:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.state = None
        self.cleared = False

    async def clear(self):
        self.data.clear()
        self.state = None
        self.cleared = True

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, state):
        self.state = state

    async def get_data(self):
        return dict(self.data)


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
        ), patch(
            "core.services.accountant_relation_service.is_user_accountant", new=AsyncMock(return_value=False)
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

    async def test_blocked_users_button_opens_full_block_menu(self):
        message = SimpleNamespace(answer=AsyncMock())
        user = SimpleNamespace(id=9)

        with patch("bot.handlers.block_manage.send_block_menu_message", new=AsyncMock()) as menu_mock:
            await panel.show_user_panel_blocked_users(message, state=SimpleNamespace(), user=user)

        menu_mock.assert_awaited_once_with(message, user)
        message.answer.assert_not_awaited()

    async def test_legacy_block_unblock_callback_rejects_delegated_accounts(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))
        status = {
            "can_block": False,
            "reason_code": BLOCK_STATUS_REASON_ACCOUNTANT_DELEGATED,
            "reason_message": "قابلیت بلاک کاربران فقط در اختیار سرگروه است.",
        }
        callback_data = panel.UserPanelBlockCallback(action="unblock", user_id=4)

        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=status)
        ), patch("core.services.block_service.unblock_user", new=AsyncMock()) as unblock_mock:
            await panel.unblock_user_from_user_panel(callback, callback_data, user=SimpleNamespace(id=9))

        callback.answer.assert_awaited_once_with(status["reason_message"], show_alert=True)
        callback.message.edit_text.assert_not_awaited()
        unblock_mock.assert_not_awaited()

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
        self.assertIn("➕ دعوت مشتری سطح1", button_texts)
        self.assertIn("➕ دعوت مشتری سطح2", button_texts)
        self.assertIn("👤 مشتری تست | سطح ۱ | فعال", button_texts)
        self.assertIn("👤 مشتری دوم | سطح ۲ | در انتظار ثبت‌نام", button_texts)

        detail_keyboard = panel.get_customer_detail_keyboard(relation)
        self.assertIn("اخراج مشتری", detail_keyboard.inline_keyboard[0][0].text)

    async def test_customer_invite_tier2_button_is_webapp_only(self):
        callback = SimpleNamespace(answer=AsyncMock())
        await panel.user_panel_customer_invite_tier2_webapp_only(callback, user=SimpleNamespace(id=1))
        callback.answer.assert_awaited_once_with(panel.USER_PANEL_INVITE_TIER2_WEBAPP_ONLY_TEXT, show_alert=True)

    async def test_customer_invite_tier1_starts_fsm_after_sync_gate(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        state = FakeState()
        user = SimpleNamespace(
            id=1,
            role=UserRole.STANDARD,
            account_status=None,
            messenger_blocked_at=None,
            messenger_grace_expires_at=None,
        )

        with patch("bot.handlers.panel._customer_invite_access_allowed", new=AsyncMock(return_value=(True, None))), patch(
            "bot.handlers.panel.check_customer_invite_sync_ready",
            new=AsyncMock(return_value=SimpleNamespace(ready=True, message=None)),
        ):
            await panel.start_user_panel_customer_invite_tier1(callback, state, user)

        self.assertEqual(state.data["customer_invite_owner_id"], 1)
        self.assertIsNotNone(state.state)
        self.assertIn("نام مشتری", callback.message.answer.await_args.args[0])

    async def test_customer_invite_confirm_forwards_signed_payload(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()))
        state = FakeState(
            {
                "customer_invite_owner_id": 1,
                "customer_invite_management_name": "مشتری تست",
                "customer_invite_mobile_number": "09123456789",
            }
        )
        user = SimpleNamespace(
            id=1,
            role=UserRole.STANDARD,
            account_status=None,
            messenger_blocked_at=None,
            messenger_grace_expires_at=None,
        )

        with patch("bot.handlers.panel._customer_invite_access_allowed", new=AsyncMock(return_value=(True, None))), patch(
            "bot.handlers.panel.check_customer_invite_sync_ready",
            new=AsyncMock(return_value=SimpleNamespace(ready=True, message=None)),
        ), patch(
            "bot.handlers.panel.forward_customer_invite_to_iran",
            new=AsyncMock(return_value=(201, {"created": True, "sms_sent": True})),
        ) as forward_mock, patch(
            "bot.handlers.panel._edit_or_answer_customers_panel",
            new=AsyncMock(),
        ):
            await panel.confirm_customer_invite_tier1(callback, state, user)

        payload = forward_mock.await_args.args[0]
        self.assertEqual(payload["account_name"], "customer_09123456789")
        self.assertEqual(payload["customer_tier"], "tier1")
        self.assertTrue(payload["idempotency_key"].startswith("customer-invite:"))
        self.assertTrue(state.cleared)
        self.assertIn("پیامک", callback.message.answer.await_args_list[0].args[0])

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

    async def test_colleagues_query_excludes_any_customer_or_accountant_relation_history(self):
        session = CapturingSession()
        await panel._load_colleagues_for_user(session, user_id=5)

        compiled = str(session.statement.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("customer_relations", compiled)
        self.assertIn("accountant_relations", compiled)
        self.assertNotIn("customer_relations.status", compiled)
        self.assertNotIn("accountant_relations.status", compiled)
        self.assertNotIn("customer_relations.deleted_at", compiled)
        self.assertNotIn("accountant_relations.deleted_at", compiled)


if __name__ == "__main__":
    unittest.main()
