"""Characterization tests for api.routers.users._limitation_notification_message.

This module covers only the pure limitation-message formatter and does not complete P1-00b or establish Offer/Request-origin interaction evidence; preserve current behavior exactly.
"""

from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import _limitation_notification_message


class TestLimitationNotificationMessage(unittest.TestCase):
    def test_limitation_notification_message_with_expiry(self):
        fixed_dt = datetime(2026, 5, 20, 14, 30, 0)
        user = SimpleNamespace(limitations_expire_at=fixed_dt)
        limitations = [
            "مجموع تعداد معاملات: 5",
            "مجموع ارسال لفظ در کانال: 10",
        ]
        sentinel = "SENTINEL-JALALI-LIMITATION"
        with patch("api.routers.users.to_jalali_str", return_value=sentinel) as mock_to_jalali:
            msg = _limitation_notification_message(user, limitations)
            mock_to_jalali.assert_called_once_with(fixed_dt)
            expected = (
                "⚠️ *اعمال محدودیت*\n\n"
                "محدودیت‌های زیر برای حساب شما اعمال شده است:\n\n"
                "• مجموع تعداد معاملات: 5\n"
                "• مجموع ارسال لفظ در کانال: 10\n"
                f"\n📅 *اعتبار تا:* {sentinel}"
            )
            self.assertEqual(msg, expected)

    def test_limitation_notification_message_without_expiry(self):
        user = SimpleNamespace(limitations_expire_at=None)
        limitations = ["مجموع تعداد کالای معامله شده: 2"]
        with patch("api.routers.users.to_jalali_str") as mock_to_jalali:
            msg = _limitation_notification_message(user, limitations)
            mock_to_jalali.assert_not_called()
            expected = (
                "⚠️ *اعمال محدودیت*\n\n"
                "محدودیت‌های زیر برای حساب شما اعمال شده است:\n\n"
                "• مجموع تعداد کالای معامله شده: 2\n"
                "\n📅 *اعتبار تا:* نامحدود"
            )
            self.assertEqual(msg, expected)
            self.assertTrue(msg.endswith("نامحدود"))

    def test_limitation_notification_message_empty_limitations_boundary(self):
        user = SimpleNamespace(limitations_expire_at=None)
        limitations = []
        with patch("api.routers.users.to_jalali_str") as mock_to_jalali:
            msg = _limitation_notification_message(user, limitations)
            mock_to_jalali.assert_not_called()
            expected = (
                "⚠️ *اعمال محدودیت*\n\n"
                "محدودیت‌های زیر برای حساب شما اعمال شده است:\n\n"
                "\n📅 *اعتبار تا:* نامحدود"
            )
            self.assertEqual(msg, expected)


if __name__ == "__main__":
    unittest.main()
