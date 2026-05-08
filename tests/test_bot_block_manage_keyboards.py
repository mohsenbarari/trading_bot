import unittest

from bot.handlers.block_manage import get_block_menu_keyboard, get_blocked_list_keyboard, get_search_results_keyboard


class BotBlockManageKeyboardTests(unittest.TestCase):
    def test_block_manage_keyboards_render_expected_buttons(self):
        menu = get_block_menu_keyboard({"can_block": True, "remaining": 2})
        self.assertEqual(len(menu.inline_keyboard), 3)

        blocked = get_blocked_list_keyboard([{"id": 1, "account_name": "u1"}, {"id": 2, "account_name": "u2"}])
        self.assertIn("u1", blocked.inline_keyboard[0][0].text)

        search = get_search_results_keyboard([
            {"id": 1, "account_name": "u1", "is_blocked": False},
            {"id": 2, "account_name": "u2", "is_blocked": True},
        ])
        self.assertIn("🚫", search.inline_keyboard[0][0].text)
        self.assertIn("✅", search.inline_keyboard[1][0].text)


if __name__ == "__main__":
    unittest.main()