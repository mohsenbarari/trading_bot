import unittest

from bot.onboarding import OFFER_TUTORIAL_TEXT


class BotOfferTutorialSettlementTests(unittest.TestCase):
    def test_tutorial_teaches_only_valid_cash_and_tomorrow_prefixes(self):
        self.assertIn("خ ن", OFFER_TUTORIAL_TEXT)
        self.assertIn("ف ن", OFFER_TUTORIAL_TEXT)
        self.assertIn("خ ن ف", OFFER_TUTORIAL_TEXT)
        self.assertIn("ف ن ف", OFFER_TUTORIAL_TEXT)
        self.assertIn("خرید نقد فردا", OFFER_TUTORIAL_TEXT)
        self.assertIn("فروش نقد فردا", OFFER_TUTORIAL_TEXT)
        self.assertIn("جای بلوک نوع معامله و تسویه آزاد است", OFFER_TUTORIAL_TEXT)
        self.assertIn("فقط یک بار نوشته شوند", OFFER_TUTORIAL_TEXT)
        self.assertIn("امام 30تا 85000 خرید نقد", OFFER_TUTORIAL_TEXT)
        self.assertIn("«خ» و «ف» به‌تنهایی معتبر نیستند", OFFER_TUTORIAL_TEXT)


if __name__ == "__main__":
    unittest.main()
