import unittest

from core.telegram_legacy_otp_relay_contract import (
    LEGACY_TELEGRAM_OTP_RELAY_PURPOSE,
    validate_legacy_telegram_otp_relay,
)


OTP_TEXT = "🔐 کد ورود شما: `12345`\n\nاین کد تا ۲ دقیقه معتبر است."


class TelegramLegacyOTPRelayContractTests(unittest.TestCase):
    def test_accepts_current_and_mixed_version_legacy_envelopes(self):
        current = validate_legacy_telegram_otp_relay(
            chat_id=7007,
            text=OTP_TEXT,
            parse_mode="Markdown",
            purpose=LEGACY_TELEGRAM_OTP_RELAY_PURPOSE,
        )
        mixed_version = validate_legacy_telegram_otp_relay(
            chat_id=7007,
            text=OTP_TEXT,
            parse_mode="Markdown",
            purpose=None,
        )

        self.assertEqual(current, mixed_version)
        self.assertEqual(current.purpose, LEGACY_TELEGRAM_OTP_RELAY_PURPOSE)

    def test_rejects_every_general_notification_shape(self):
        invalid_cases = (
            {"chat_id": 0, "text": OTP_TEXT, "parse_mode": "Markdown"},
            {"chat_id": 7007, "text": "پیام عمومی", "parse_mode": "Markdown"},
            {"chat_id": 7007, "text": OTP_TEXT, "parse_mode": "HTML"},
            {
                "chat_id": 7007,
                "text": OTP_TEXT,
                "parse_mode": "Markdown",
                "purpose": "general_notification",
            },
            {
                "chat_id": 7007,
                "text": OTP_TEXT.replace("12345", "۱۲۳۴۵"),
                "parse_mode": "Markdown",
            },
        )
        for payload in invalid_cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    validate_legacy_telegram_otp_relay(**payload)


if __name__ == "__main__":
    unittest.main()
