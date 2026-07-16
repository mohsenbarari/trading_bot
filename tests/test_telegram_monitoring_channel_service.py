import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.services.telegram_monitoring_channel_service import (
    build_monitoring_offer_message,
    build_monitoring_offer_presenter,
    normalize_mobile_number,
    monitoring_delivery_enabled,
    monitoring_enqueue_enabled,
)


class TelegramMonitoringChannelServiceTests(unittest.TestCase):
    def test_enqueue_flag_is_separate_from_foreign_delivery_credentials(self):
        with patch(
            "core.services.telegram_monitoring_channel_service.settings.telegram_monitoring_channel_enabled",
            True,
        ), patch(
            "core.services.telegram_monitoring_channel_service.settings.telegram_monitoring_bot_token",
            None,
        ), patch(
            "core.services.telegram_monitoring_channel_service.settings.telegram_monitoring_channel_id",
            None,
        ):
            self.assertTrue(monitoring_enqueue_enabled())
            self.assertFalse(monitoring_delivery_enabled())

    def test_normalize_mobile_number_keeps_full_digits(self):
        self.assertEqual(normalize_mobile_number("09122503501"), "09122503501")
        self.assertEqual(normalize_mobile_number("+98 912 250 3501"), "989122503501")
        self.assertEqual(normalize_mobile_number("bad"), "")

    def test_monitoring_message_uses_telegram_username_without_project_names(self):
        user = SimpleNamespace(
            id=15,
            username="coin_user",
            mobile_number="09122503501",
            role=SimpleNamespace(value="عادی"),
            account_name="internal_account",
            full_name="Sensitive Full Name",
        )
        owner = SimpleNamespace(
            id=9,
            username=None,
            mobile_number="09370809280",
            full_name="سرگروه تست",
            account_name="owner_account",
        )
        offer = SimpleNamespace(
            offer_public_id="ofr_test",
            offer_type=SimpleNamespace(value="sell"),
            settlement_type=SimpleNamespace(value="tomorrow"),
            commodity=SimpleNamespace(name="ربع بهار"),
            quantity=40,
            price=190000,
            status=SimpleNamespace(value="active"),
            home_server="foreign",
            notes="",
        )

        presenter = build_monitoring_offer_presenter(user, customer_owner=owner)
        message = build_monitoring_offer_message(offer, presenter)

        self.assertIn("رصد بازار", message)
        self.assertIn("فروش ربع بهار 40 عدد فردا", message)
        self.assertIn("ارسال شده از: foreign", message)
        self.assertIn("نام کاربری آفر‌دهنده: internal_account", message)
        self.assertIn("یوزرنیم تلگرام: @coin_user", message)
        self.assertIn("موبایل: 09122503501", message)
        self.assertIn("مالک مشتری:", message)
        self.assertIn("نام سرگروه: سرگروه تست", message)
        self.assertIn("یوزرنیم تلگرام: ", message)
        self.assertNotIn("شناسه آفر:", message)
        self.assertNotIn("ثبت‌کننده:", message)
        self.assertNotIn("سرور مرجع:", message)
        self.assertNotIn("Sensitive Full Name", message)


if __name__ == "__main__":
    unittest.main()
