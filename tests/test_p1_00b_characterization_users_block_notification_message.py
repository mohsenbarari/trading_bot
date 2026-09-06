"""Characterization tests for api.routers.users._block_notification_message.

This module covers only the pure block-message formatter and does not complete P1-00b or establish origin-interaction evidence.
"""

from datetime import datetime
import unittest
from unittest.mock import patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import _block_notification_message


class TestBlockNotificationMessage(unittest.TestCase):
    def test_block_notification_message_temporary_boundary_year_2100(self):
        restricted_until = datetime(2100, 12, 31, 23, 59, 59)
        sentinel = "SENTINEL-JALALI"
        with patch("api.routers.users.to_jalali_str", return_value=sentinel) as mock_to_jalali:
            msg = _block_notification_message(restricted_until)
            mock_to_jalali.assert_called_once_with(restricted_until)
            expected = (
                "⛔ *اخطار مسدودیت حساب*\n\n"
                "حساب کاربری شما موقتاً مسدود شده است.\n\n"
                "📅 *پایان مسدودیت:* SENTINEL-JALALI\n\n"
                "تا زمان رفع مسدودیت امکان انجام معاملات وجود ندارد."
            )
            self.assertEqual(msg, expected)

    def test_block_notification_message_permanent_branch_year_2101(self):
        restricted_until = datetime(2101, 1, 1, 0, 0, 0)
        sentinel = "SENTINEL-JALALI"
        with patch("api.routers.users.to_jalali_str", return_value=sentinel) as mock_to_jalali:
            msg = _block_notification_message(restricted_until)
            mock_to_jalali.assert_called_once_with(restricted_until)
            expected = (
                "⛔ *اخطار مسدودیت حساب*\n\n"
                "حساب کاربری شما به صورت *دائمی* مسدود شده است.\n"
                "برای اطلاعات بیشتر با پشتیبانی تماس بگیرید."
            )
            self.assertEqual(msg, expected)
            self.assertNotIn(sentinel, msg)


if __name__ == "__main__":
    unittest.main()
