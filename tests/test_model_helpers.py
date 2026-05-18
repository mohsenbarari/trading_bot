import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from models.database import get_db
from models.trading_setting import TradingSetting
from models.user import User, UserRole, set_legacy_has_bot_access_compatibility
from models.user_block import UserBlock


class UserSoftDeleteTests(unittest.TestCase):
    def test_soft_delete_marks_deleted_and_frees_unique_fields_when_id_exists(self):
        user = User(
            id=42,
            account_name='sample_user',
            mobile_number='09123456789',
            telegram_id=99887766,
            full_name='Sample User',
            address='Tehran',
            role=UserRole.STANDARD,
        )

        user.soft_delete()

        self.assertTrue(user.is_deleted)
        self.assertIsInstance(user.deleted_at, datetime)
        self.assertEqual(user.account_name, 'sample_user_del_42')
        self.assertEqual(user.mobile_number, '09123456789_del_42')
        self.assertIsNone(user.telegram_id)

    def test_soft_delete_keeps_names_stable_when_model_has_no_persisted_id(self):
        user = User(
            account_name='fresh_user',
            mobile_number='09999999999',
            telegram_id=123456,
            full_name='Fresh User',
            address='Shiraz',
            role=UserRole.WATCH,
        )

        user.soft_delete()

        self.assertTrue(user.is_deleted)
        self.assertIsInstance(user.deleted_at, datetime)
        self.assertEqual(user.account_name, 'fresh_user')
        self.assertEqual(user.mobile_number, '09999999999')
        self.assertIsNone(user.telegram_id)

    def test_set_legacy_has_bot_access_compatibility_updates_real_and_fake_users(self):
        persisted_user = User(
            account_name='compat_user',
            mobile_number='09120000000',
            full_name='Compat User',
            address='Tehran',
            role=UserRole.STANDARD,
        )
        fake_user = SimpleNamespace(has_bot_access=True)

        set_legacy_has_bot_access_compatibility(persisted_user, enabled=False)
        set_legacy_has_bot_access_compatibility(fake_user, enabled=False)

        self.assertFalse(persisted_user.has_bot_access)
        self.assertFalse(fake_user.has_bot_access)


class ModelReprTests(unittest.TestCase):
    def test_user_block_repr_includes_blocker_and_blocked_ids(self):
        user_block = UserBlock(blocker_id=11, blocked_id=22)

        self.assertEqual(repr(user_block), '<UserBlock(blocker=11, blocked=22)>')

    def test_trading_setting_repr_includes_key_and_value(self):
        trading_setting = TradingSetting(key='offer_expiry_minutes', value='2')

        self.assertEqual(repr(trading_setting), '<TradingSetting offer_expiry_minutes=2>')


class ModelDatabaseHelperTests(unittest.TestCase):
    def test_get_db_delegates_to_core_db_helper(self):
        sentinel = object()

        with patch('core.db.get_db', return_value=sentinel) as delegated_get_db:
            result = get_db()

        delegated_get_db.assert_called_once_with()
        self.assertIs(result, sentinel)


if __name__ == '__main__':
    unittest.main()