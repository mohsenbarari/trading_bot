import unittest

from bot.onboarding import OFFER_TUTORIAL_TEXT


class BotOfferTutorialSettlementTests(unittest.TestCase):
    def test_tutorial_teaches_only_valid_cash_and_tomorrow_prefixes(self):
        self.assertIn("«خ» یا «خرید»", OFFER_TUTORIAL_TEXT)
        self.assertIn("«ف» یا «فروش»", OFFER_TUTORIAL_TEXT)
        self.assertIn("«خ ف» یا «خ‌ف» یا «خف»", OFFER_TUTORIAL_TEXT)
        self.assertIn("«ف ف» یا «ف‌ف» یا «فف»", OFFER_TUTORIAL_TEXT)
        self.assertIn("جای بلوک نوع معامله و تسویه آزاد است", OFFER_TUTORIAL_TEXT)
        self.assertIn("فقط یک بار نوشته شوند", OFFER_TUTORIAL_TEXT)
        self.assertIn("امام 30تا 85000 خرید", OFFER_TUTORIAL_TEXT)
        self.assertIn("نام کالا اختیاری است", OFFER_TUTORIAL_TEXT)


if __name__ == "__main__":
    unittest.main()
