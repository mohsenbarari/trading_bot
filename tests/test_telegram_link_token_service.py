import unittest

from core.services.telegram_link_token_service import (
    build_telegram_deep_link,
    build_telegram_start_parameter,
    generate_telegram_link_token,
    hash_telegram_link_token,
)


class TelegramLinkTokenServiceTests(unittest.TestCase):
    def test_generated_token_fits_telegram_start_parameter_limit(self):
        token = generate_telegram_link_token()
        start_parameter = build_telegram_start_parameter(token)

        self.assertTrue(start_parameter.startswith("link_"))
        self.assertLessEqual(len(start_parameter), 64)
        self.assertEqual(len(hash_telegram_link_token(token)), 64)

    def test_deep_link_uses_clean_bot_username(self):
        self.assertEqual(
            build_telegram_deep_link("@example_bot", "abc123"),
            "https://t.me/example_bot?start=link_abc123",
        )


if __name__ == "__main__":
    unittest.main()
