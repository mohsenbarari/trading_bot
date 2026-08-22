import unittest

from bot.onboarding import OFFER_TUTORIAL_TEXT


class BotOfferTutorialSettlementTests(unittest.TestCase):
    def test_tutorial_is_compact_and_teaches_the_current_offer_contract(self):
        self.assertLess(len(OFFER_TUTORIAL_TEXT), 900)
        self.assertIn("«خ» برای خرید و «ف» برای فروش", OFFER_TUTORIAL_TEXT)
        self.assertIn("«خ ف» برای خرید و «ف ف» برای فروش", OFFER_TUTORIAL_TEXT)
        self.assertIn("«197» یعنی «197000»", OFFER_TUTORIAL_TEXT)
        self.assertIn("بدون نام کالا", OFFER_TUTORIAL_TEXT)
        self.assertIn("برای تاریخ پایین «پ»", OFFER_TUTORIAL_TEXT)
        self.assertIn("برای پک «پک»", OFFER_TUTORIAL_TEXT)
        self.assertIn("پک همیشه ۱۰۰ عدد و یکجا است", OFFER_TUTORIAL_TEXT)
        self.assertIn("جمع آن‌ها باید برابر تعداد باشد", OFFER_TUTORIAL_TEXT)
        self.assertIn("توضیحات را بعد از «:»", OFFER_TUTORIAL_TEXT)
        self.assertIn("خ ربع 20تا 51500", OFFER_TUTORIAL_TEXT)
        self.assertIn("ف ف نیم 10تا 125000 : شب حساب", OFFER_TUTORIAL_TEXT)
        self.assertIn("خ پک 100600", OFFER_TUTORIAL_TEXT)

        self.assertNotIn("سیستم آن را فقط از روی بازهٔ قیمت", OFFER_TUTORIAL_TEXT)
        self.assertNotIn("عدد چهاررقمی کوتاه", OFFER_TUTORIAL_TEXT)


if __name__ == "__main__":
    unittest.main()
