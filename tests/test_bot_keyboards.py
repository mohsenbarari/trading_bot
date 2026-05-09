import unittest

from core.enums import UserRole
from bot import keyboards


class BotKeyboardsTests(unittest.TestCase):
    def test_primary_menu_keyboards(self):
        inline = keyboards.get_create_token_inline_keyboard()
        self.assertEqual(inline.inline_keyboard[0][0].callback_data, 'create_invitation_inline')

        super_admin_menu = keyboards.get_persistent_menu_keyboard(UserRole.SUPER_ADMIN, 'https://mini-app')
        super_admin_texts = [button.text for row in super_admin_menu.keyboard for button in row]
        self.assertIn('🔐 پنل مدیریت', super_admin_texts)

        standard_menu = keyboards.get_persistent_menu_keyboard(UserRole.STANDARD, 'https://mini-app')
        standard_texts = [button.text for row in standard_menu.keyboard for button in row]
        self.assertIn('⚙️ تنظیمات', standard_texts)

        user_panel = keyboards.get_user_panel_keyboard(UserRole.MIDDLE_MANAGER)
        user_panel_texts = [button.text for row in user_panel.keyboard for button in row]
        self.assertIn('⚙️ تنظیمات کاربری', user_panel_texts)
        self.assertIn('🔙 بازگشت', user_panel_texts)

    def test_admin_and_user_management_keyboards(self):
        admin_panel = keyboards.get_admin_panel_keyboard()
        admin_texts = [button.text for row in admin_panel.keyboard for button in row]
        self.assertIn('👥 مدیریت کاربران', admin_texts)

        users_management = keyboards.get_users_management_keyboard()
        management_texts = [button.text for row in users_management.keyboard for button in row]
        self.assertEqual(management_texts, ['📋 لیست کاربران', '🔍 جستجوی کاربر', '🔙 بازگشت به پنل مدیریت'])

    def test_users_list_and_profile_keyboards(self):
        users = [
            type('User', (), {'id': 1, 'account_name': 'one', 'full_name': 'One', 'mobile_number': '1'})(),
            type('User', (), {'id': 2, 'account_name': 'two', 'full_name': 'Two', 'mobile_number': '2'})(),
            type('User', (), {'id': 3, 'account_name': 'three', 'full_name': 'Three', 'mobile_number': '3'})(),
            type('User', (), {'id': 4, 'account_name': 'four', 'full_name': 'Four', 'mobile_number': '4'})(),
        ]
        keyboard = keyboards.get_users_list_inline_keyboard(users, page=2, total_count=25, limit=10)
        self.assertEqual(keyboard.inline_keyboard[-1][1].text, '2/3')
        self.assertEqual(keyboard.inline_keyboard[0][0].callback_data, 'user_profile_1')

        profile_keyboard = keyboards.get_user_profile_return_keyboard(7, back_to_page=3, is_restricted=True, has_limitations=True)
        first_row = profile_keyboard.inline_keyboard[0]
        self.assertEqual(first_row[0].callback_data, 'user_unblock_7')
        self.assertEqual(first_row[1].callback_data, 'user_unlimit_7')
        self.assertEqual(profile_keyboard.inline_keyboard[-1][0].callback_data, 'users_page_3')

    def test_user_settings_and_role_keyboards(self):
        settings_keyboard = keyboards.get_user_settings_keyboard(5, can_block=False, max_blocked=3)
        texts = [button.text for row in settings_keyboard.inline_keyboard for button in row]
        self.assertIn('🤖 تغییر دسترسی بات', texts)
        self.assertIn('🚫 تنظیمات بلاک (غیرفعال - 3)', texts)

        role_select = keyboards.get_role_selection_keyboard()
        role_values = [row[0].text for row in role_select.inline_keyboard[:-1]]
        self.assertNotIn(UserRole.SUPER_ADMIN.value, role_values)
        self.assertEqual(role_select.inline_keyboard[-1][0].callback_data, 'comm_fsm_cancel')

        role_edit = keyboards.get_user_role_edit_keyboard(9)
        self.assertEqual(role_edit.inline_keyboard[-1][0].callback_data, 'user_profile_9')

    def test_limit_and_block_option_keyboards(self):
        block_duration = keyboards.get_block_duration_keyboard(8)
        self.assertEqual(block_duration.inline_keyboard[-1][0].callback_data, 'user_settings_8')

        limit_duration = keyboards.get_limit_duration_keyboard(8)
        self.assertEqual(limit_duration.inline_keyboard[-1][0].callback_data, 'user_settings_8')

        limit_settings = keyboards.get_limit_settings_keyboard(4, max_trades=1, max_commodities=2, max_requests=3)
        self.assertEqual(limit_settings.inline_keyboard[-1][0].callback_data, 'limit_confirm_4')

        skip_keyboard = keyboards.get_skip_keyboard('skip-me')
        self.assertEqual(skip_keyboard.inline_keyboard[0][0].callback_data, 'skip-me')

        block_settings = keyboards.get_block_settings_keyboard(6, can_block=True, max_blocked=10)
        self.assertEqual(block_settings.inline_keyboard[0][0].callback_data, 'admin_toggle_block_6')

        max_block = keyboards.get_max_block_options_keyboard(6)
        self.assertEqual(max_block.inline_keyboard[-2][0].callback_data, 'admin_max_block_custom_6')
        self.assertEqual(max_block.inline_keyboard[-1][0].callback_data, 'user_block_settings_6')

    def test_miscellaneous_keyboards(self):
        delete_confirm = keyboards.get_user_delete_confirm_keyboard(12)
        self.assertEqual(delete_confirm.inline_keyboard[0][0].callback_data, 'user_delete_confirm_12')

        mini_app = keyboards.get_mini_app_keyboard('https://mini-app')
        self.assertEqual(mini_app.inline_keyboard[0][0].web_app.url, 'https://mini-app')

        share_contact = keyboards.get_share_contact_keyboard()
        self.assertTrue(share_contact.keyboard[0][0].request_contact)

        cancel_keyboard = keyboards.get_commodity_fsm_cancel_keyboard()
        self.assertEqual(cancel_keyboard.inline_keyboard[0][0].callback_data, 'comm_fsm_cancel')

        commodity_delete = keyboards.get_commodity_delete_confirm_keyboard(13)
        self.assertEqual(commodity_delete.inline_keyboard[0][0].callback_data, 'comm_delete_confirm_yes_13')

    def test_commodity_alias_keyboards(self):
        commodity = {
            'id': 3,
            'aliases': [
                {'id': 1, 'alias': 'gold'},
                {'id': 2, 'alias': 'coin'},
            ],
        }
        aliases_keyboard = keyboards.get_aliases_list_keyboard(commodity)
        self.assertEqual(aliases_keyboard.inline_keyboard[0][1].callback_data, 'alias_edit_3_1')
        self.assertEqual(aliases_keyboard.inline_keyboard[1][2].callback_data, 'alias_delete_3_2')
        self.assertEqual(aliases_keyboard.inline_keyboard[-1][0].callback_data, 'comm_back_to_list')

        alias_delete = keyboards.get_alias_delete_confirm_keyboard(3, 2)
        self.assertEqual(alias_delete.inline_keyboard[0][0].callback_data, 'alias_delete_confirm_yes_3_2')


if __name__ == '__main__':
    unittest.main()