import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.services.telegram_monitoring_channel_service import (
    build_channel_post_url,
    build_monitoring_channel_reply_markup,
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

    def test_channel_post_url_uses_private_channel_internal_id(self):
        self.assertEqual(
            build_channel_post_url("-1003940886636", 777),
            "https://t.me/c/3940886636/777",
        )
        self.assertIsNone(build_channel_post_url("-1003940886636", None))

    def test_monitoring_reply_markup_links_to_main_channel_post(self):
        offer = SimpleNamespace(channel_message_id=777)

        with patch("core.services.telegram_monitoring_channel_service.settings.channel_id", -1003940886636):
            markup = build_monitoring_channel_reply_markup(offer)

        self.assertEqual(
            markup,
            {
                "inline_keyboard": [
                    [
                        {
                            "text": "مشاهده پست در کانال اصلی",
                            "url": "https://t.me/c/3940886636/777",
                        }
                    ]
                ]
            },
        )

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
            channel_message_id=777,
            notes="",
        )

        presenter = build_monitoring_offer_presenter(user, customer_owner=owner)
        message = build_monitoring_offer_message(offer, presenter)

        self.assertIn("فروش ربع بهار 40 عدد فردا", message)
        self.assertIn("لفظ دهنده: internal_account", message)
        self.assertIn("<blockquote expandable>", message)
        self.assertIn("ارسال شده از: بات", message)
        self.assertIn("یوزرنیم تلگرام: @coin_user", message)
        self.assertIn("موبایل: 09122503501", message)
        self.assertIn("مالک مشتری:", message)
        self.assertIn("نام سرگروه: سرگروه تست", message)
        self.assertIn("یوزرنیم تلگرام: ", message)
        self.assertNotIn("شناسه آفر:", message)
        self.assertNotIn("ثبت‌کننده:", message)
        self.assertNotIn("سرور مرجع:", message)
        self.assertNotIn("آفر‌دهنده", message)
        self.assertNotIn("نام کاربری لفظ دهنده", message)
        self.assertNotIn("رصد بازار", message)
        self.assertNotIn("Sensitive Full Name", message)

    def test_monitoring_message_maps_iran_origin_to_webapp(self):
        offer = SimpleNamespace(
            offer_type=SimpleNamespace(value="buy"),
            settlement_type=SimpleNamespace(value="cash"),
            commodity=SimpleNamespace(name="امام"),
            quantity=1,
            price=100,
            status=SimpleNamespace(value="active"),
            home_server="iran",
            notes="",
        )
        presenter = build_monitoring_offer_presenter(
            SimpleNamespace(
                id=1,
                account_name="web_user",
                username=None,
                mobile_number="09120000000",
                role=SimpleNamespace(value="عادی"),
            )
        )

        message = build_monitoring_offer_message(offer, presenter)

        self.assertIn("ارسال شده از: وب اپ", message)

    def test_monitoring_message_escapes_html_in_visible_and_expandable_parts(self):
        offer = SimpleNamespace(
            offer_type=SimpleNamespace(value="buy"),
            settlement_type=SimpleNamespace(value="cash"),
            commodity=SimpleNamespace(name="امام <x>"),
            quantity=1,
            price=100,
            status=SimpleNamespace(value="active"),
            home_server="iran",
            notes="A & B",
        )
        presenter = build_monitoring_offer_presenter(
            SimpleNamespace(
                id=1,
                account_name="user <bad>",
                username="tg&name",
                mobile_number="09120000000",
                role=SimpleNamespace(value="عادی"),
            )
        )

        message = build_monitoring_offer_message(offer, presenter)

        self.assertIn("امام &lt;x&gt;", message)
        self.assertIn("توضیحات: A &amp; B", message)
        self.assertIn("لفظ دهنده: user &lt;bad&gt;", message)
        self.assertIn("@tg&amp;name", message)


if __name__ == "__main__":
    unittest.main()
