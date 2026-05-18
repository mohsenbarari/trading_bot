from datetime import datetime
import unittest

from pydantic import ValidationError

from core.enums import NotificationCategory, NotificationLevel, UserRole
from core.utils import to_jalali_str
from schemas import (
    InvitationCreate,
    NotificationRead,
    OTPRequest,
    OTPVerify,
    TokenPair,
    UserPublicRead,
    UserRead,
)


class SchemaSmokeTests(unittest.TestCase):
    def test_mobile_schemas_normalize_persian_digits(self):
        otp_request = OTPRequest(mobile_number='۰۹۱۲۳۴۵۶۷۸۹')
        otp_verify = OTPVerify(mobile_number='۰۹۱۲۳۴۵۶۷۸۹', otp_code='12345')
        invitation = InvitationCreate(
            account_name='demo-user',
            mobile_number='۰۹۱۲۳۴۵۶۷۸۹',
            role=UserRole.WATCH,
        )

        self.assertEqual(otp_request.mobile_number, '09123456789')
        self.assertEqual(otp_verify.mobile_number, '09123456789')
        self.assertEqual(invitation.mobile_number, '09123456789')

    def test_mobile_schemas_reject_invalid_numbers(self):
        with self.assertRaises(ValidationError):
            OTPRequest(mobile_number='09123')

        with self.assertRaises(ValidationError):
            InvitationCreate(account_name='bad', mobile_number='989123456789')

    def test_token_pair_defaults_match_auth_contract(self):
        token_pair = TokenPair(access_token='access', refresh_token='refresh')

        self.assertEqual(token_pair.token_type, 'bearer')
        self.assertEqual(token_pair.expires_in, 1800)

    def test_user_read_and_public_read_expose_jalali_helpers(self):
        created_at = datetime(2026, 5, 1, 10, 30, 0)
        restricted_until = datetime(2026, 5, 2, 11, 45, 0)
        limitations_expire_at = datetime(2026, 5, 3, 12, 15, 0)
        grace_expires_at = datetime(2026, 5, 4, 13, 0, 0)
        locked_at = datetime(2026, 5, 5, 14, 30, 0)

        user = UserRead(
            id=1,
            telegram_id=None,
            username='demo',
            full_name='Demo User',
            account_name='demo-user',
            mobile_number='09123456789',
            role=UserRole.STANDARD,
            messenger_grace_expires_at=grace_expires_at,
            messenger_blocked_at=locked_at,
            has_bot_access=True,
            is_deleted=False,
            created_at=created_at,
            trading_restricted_until=restricted_until,
            max_daily_trades=10,
            max_active_commodities=5,
            max_daily_requests=20,
            limitations_expire_at=limitations_expire_at,
            trades_count=1,
            commodities_traded_count=2,
            channel_messages_count=3,
            last_seen_at=None,
            can_block_users=True,
            max_blocked_users=10,
            max_sessions=1,
        )
        public_user = UserPublicRead(
            id=1,
            account_name='demo-user',
            role=UserRole.STANDARD,
            mobile_number='09123456789',
            address='Tehran',
            created_at=created_at,
            trades_count=7,
            last_seen_at=None,
        )

        self.assertEqual(user.created_at_jalali, to_jalali_str(created_at))
        self.assertEqual(user.global_lock_grace_expires_at, grace_expires_at)
        self.assertEqual(user.global_web_locked_at, locked_at)
        self.assertEqual(user.trading_restricted_until_jalali, to_jalali_str(restricted_until))
        self.assertEqual(user.limitations_expire_at_jalali, to_jalali_str(limitations_expire_at))
        self.assertEqual(public_user.created_at_jalali, to_jalali_str(created_at))

    def test_notification_read_exposes_level_category_and_jalali_helper(self):
        created_at = datetime(2026, 5, 4, 8, 0, 0)
        notification = NotificationRead(
            id=11,
            message='demo',
            is_read=False,
            created_at=created_at,
            level=NotificationLevel.INFO,
            category=NotificationCategory.SYSTEM,
        )

        self.assertEqual(notification.level, NotificationLevel.INFO)
        self.assertEqual(notification.category, NotificationCategory.SYSTEM)
        self.assertEqual(notification.created_at_jalali, to_jalali_str(created_at))


if __name__ == '__main__':
    unittest.main()