import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from bot.callbacks import CommodityCatalogPageCallback
from bot.handlers import commodity_catalog
from bot.keyboards import get_admin_panel_keyboard, get_persistent_menu_keyboard
from core.enums import UserAccountStatus, UserRole


def make_commodity(commodity_id, name, aliases=None):
    return SimpleNamespace(
        id=commodity_id,
        name=name,
        aliases=[SimpleNamespace(alias=alias) for alias in (aliases or [])],
    )


def make_message():
    return SimpleNamespace(
        bot=SimpleNamespace(),
        chat=SimpleNamespace(id=45),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=91)),
    )


class BotCommodityCatalogTests(unittest.IsolatedAsyncioTestCase):
    def test_catalog_text_lists_commodities_aliases_and_deduplicates_main_name(self):
        commodities = [
            make_commodity(2, "ربع بهار", ["ربع", "ربع بهار", "ربعی"]),
            make_commodity(1, "امام", ["امامی", "تمام"]),
            make_commodity(3, "نیم بهار", []),
        ]

        text = commodity_catalog.build_commodity_catalog_text(commodities, page=1, page_size=10)

        self.assertIn("📦 لیست کالاها و نام‌های مستعار", text)
        self.assertIn("1. امام", text)
        self.assertIn("نام‌های قابل استفاده: امامی، تمام", text)
        self.assertIn("2. ربع بهار", text)
        self.assertIn("نام‌های قابل استفاده: ربع، ربعی", text)
        self.assertNotIn("ربع، ربع بهار، ربعی", text)
        self.assertIn("3. نیم بهار", text)
        self.assertIn("نام مستعار ثبت نشده است.", text)
        self.assertNotIn("ویرایش", text)
        self.assertNotIn("حذف", text)

    def test_catalog_empty_state_and_page_keyboard(self):
        self.assertIn(
            "هنوز کالایی ثبت نشده است",
            commodity_catalog.build_commodity_catalog_text([], page=1),
        )
        self.assertIsNone(commodity_catalog.build_commodity_catalog_keyboard(page=1, total_count=10, page_size=10))

        keyboard = commodity_catalog.build_commodity_catalog_keyboard(page=1, total_count=21, page_size=10)

        self.assertIsNotNone(keyboard)
        first_row = keyboard.inline_keyboard[0]
        self.assertEqual(first_row[0].callback_data, "noop")
        self.assertEqual(first_row[1].text, "1/3")
        self.assertEqual(first_row[2].callback_data, CommodityCatalogPageCallback(page=2).pack())

    def test_catalog_text_is_bounded_for_telegram_limit(self):
        long_alias = "نام_مستعار_" + ("خیلی_طولانی_" * 120)
        commodities = [
            make_commodity(index, f"کالای تستی {index}", [f"{long_alias}_{alias_index}" for alias_index in range(3)])
            for index in range(1, 11)
        ]

        text = commodity_catalog.build_commodity_catalog_text(commodities, page=1, page_size=10)

        self.assertLessEqual(len(text), commodity_catalog._MAX_CATALOG_TEXT_LENGTH)
        self.assertNotIn(commodity_catalog._CATALOG_TRUNCATED_NOTICE, text)

    def test_catalog_text_truncates_oversized_pages(self):
        long_alias = "نام_مستعار_" + ("خیلی_طولانی_" * 120)
        commodities = [
            make_commodity(index, f"کالای تستی {index}", [f"{long_alias}_{alias_index}" for alias_index in range(3)])
            for index in range(1, 51)
        ]

        text = commodity_catalog.build_commodity_catalog_text(commodities, page=1, page_size=50)

        self.assertLessEqual(len(text), commodity_catalog._MAX_CATALOG_TEXT_LENGTH)
        self.assertIn(commodity_catalog._CATALOG_TRUNCATED_NOTICE, text)

    def test_catalog_keyboard_clamps_out_of_range_pages(self):
        keyboard = commodity_catalog.build_commodity_catalog_keyboard(page=999, total_count=21, page_size=10)

        self.assertIsNotNone(keyboard)
        first_row = keyboard.inline_keyboard[0]
        self.assertEqual(first_row[0].callback_data, CommodityCatalogPageCallback(page=2).pack())
        self.assertEqual(first_row[1].text, "3/3")
        self.assertEqual(first_row[2].callback_data, "noop")

        keyboard = commodity_catalog.build_commodity_catalog_keyboard(page=0, total_count=21, page_size=10)

        self.assertIsNotNone(keyboard)
        first_row = keyboard.inline_keyboard[0]
        self.assertEqual(first_row[0].callback_data, "noop")
        self.assertEqual(first_row[1].text, "1/3")
        self.assertEqual(first_row[2].callback_data, CommodityCatalogPageCallback(page=2).pack())

    def test_persistent_menu_includes_read_only_catalog_for_all_roles(self):
        for role in (UserRole.STANDARD, UserRole.POLICE, UserRole.MIDDLE_MANAGER, UserRole.SUPER_ADMIN, UserRole.WATCH):
            with self.subTest(role=role):
                keyboard = get_persistent_menu_keyboard(role, "https://app.example")
                all_texts = [button.text for row in keyboard.keyboard for button in row]
                self.assertIn(commodity_catalog.COMMODITY_CATALOG_TEXT, all_texts)

    def test_admin_panel_keeps_management_button(self):
        keyboard = get_admin_panel_keyboard(UserRole.SUPER_ADMIN)
        all_texts = [button.text for row in keyboard.keyboard for button in row]

        self.assertIn("📦 مدیریت کالاها", all_texts)

    async def test_show_commodity_catalog_requires_user_and_sets_anchor(self):
        message = make_message()

        await commodity_catalog.show_commodity_catalog(message, user=None)

        message.answer.assert_not_awaited()

        message = make_message()
        await commodity_catalog.show_commodity_catalog(
            message,
            user=SimpleNamespace(is_deleted=True, account_status=SimpleNamespace(value="active")),
        )
        message.answer.assert_not_awaited()

        message = make_message()
        await commodity_catalog.show_commodity_catalog(
            message,
            user=SimpleNamespace(
                is_deleted=False,
                account_status=UserAccountStatus.INACTIVE,
                messenger_blocked_at=object(),
                messenger_grace_expires_at=None,
            ),
        )
        message.answer.assert_not_awaited()

        message = make_message()
        user = SimpleNamespace(is_deleted=False, account_status=SimpleNamespace(value="active"))
        with patch(
            "bot.handlers.commodity_catalog.delete_previous_anchor", new=AsyncMock()
        ) as delete_anchor, patch(
            "bot.handlers.commodity_catalog._render_catalog",
            new=AsyncMock(return_value=("TEXT", "KB")),
        ), patch("bot.handlers.commodity_catalog.set_anchor") as set_anchor:
            await commodity_catalog.show_commodity_catalog(message, user=user)

        delete_anchor.assert_awaited_once()
        message.answer.assert_awaited_once_with("TEXT", reply_markup="KB")
        set_anchor.assert_called_once_with(45, 91)

    async def test_show_commodity_catalog_falls_back_when_telegram_rejects_message(self):
        message = make_message()
        message.answer = AsyncMock(
            side_effect=[
                TelegramBadRequest(method=SendMessage(chat_id=45, text="TEXT"), message="Bad Request"),
                SimpleNamespace(message_id=92),
            ]
        )
        user = SimpleNamespace(is_deleted=False, account_status=SimpleNamespace(value="active"))
        with patch(
            "bot.handlers.commodity_catalog.delete_previous_anchor", new=AsyncMock()
        ), patch(
            "bot.handlers.commodity_catalog._render_catalog",
            new=AsyncMock(return_value=("TEXT", "KB")),
        ), patch("bot.handlers.commodity_catalog.set_anchor") as set_anchor, patch(
            "bot.handlers.commodity_catalog.logger.warning"
        ):
            await commodity_catalog.show_commodity_catalog(message, user=user)

        self.assertEqual(message.answer.await_count, 2)
        message.answer.assert_any_await("TEXT", reply_markup="KB")
        message.answer.assert_any_await(commodity_catalog._CATALOG_SEND_ERROR_TEXT)
        set_anchor.assert_called_once_with(45, 92)

    async def test_paginate_commodity_catalog_requires_user_and_edits_message(self):
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )

        await commodity_catalog.paginate_commodity_catalog(
            callback,
            SimpleNamespace(page=2),
            user=None,
        )

        callback.message.edit_text.assert_not_awaited()
        callback.answer.assert_awaited_once_with("دسترسی ندارید.", show_alert=True)

        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        user = SimpleNamespace(is_deleted=False, account_status=SimpleNamespace(value="active"))
        with patch(
            "bot.handlers.commodity_catalog._render_catalog",
            new=AsyncMock(return_value=("PAGE 2", "KB")),
        ) as render_mock:
            await commodity_catalog.paginate_commodity_catalog(
                callback,
                SimpleNamespace(page=2),
                user=user,
            )

        render_mock.assert_awaited_once_with(2)
        callback.message.edit_text.assert_awaited_once_with("PAGE 2", reply_markup="KB")
        callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
