from decimal import Decimal
import unittest

from telegram_price_collector.parsers import parse_message, should_ignore_message


class ParserTests(unittest.TestCase):
    def test_abshdh_extracts_melted_and_ignores_derived_gram_price(self) -> None:
        text = """
        🔺#آبشده‌حواله 0 80,150,000
        🔺#گرم‌طلا: 18,503,128
        Just In Time 21:58:35
        """
        events = parse_message("abshdh", text)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].instrument, "MELTED_GOLD")
        self.assertEqual(events[0].price, Decimal("80150000"))
        self.assertEqual(events[0].market_label, "آبشده حواله")
        self.assertEqual(events[0].trade_form, "PAPER")
        self.assertEqual(events[0].settlement_term, "UNKNOWN")
        self.assertEqual(events[0].currency, "IRT")
        self.assertEqual(events[0].price_unit, "IRT_PER_MESGHAL_750")
        self.assertEqual(events[0].source_datetime_text, "21:58:35")

    def test_naghdp_extracts_today_buy_and_ignores_gram(self) -> None:
        events = parse_message(
            "NaghdP",
            "78,750,000☀️امروز🔵خرید\nگرم: 18,179,509",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].instrument, "MELTED_GOLD_FLOW")
        self.assertEqual(events[0].settlement_term, "TODAY")
        self.assertEqual(events[0].trade_form, "PAPER")
        self.assertEqual(events[0].event_type, "OFFER")
        self.assertEqual(events[0].side, "BUY")
        self.assertEqual(events[0].price, Decimal("78750000"))

    def test_naghdp_maps_bahavaleh_to_tomorrow_sell(self) -> None:
        events = parse_message(
            "NaghdP",
            "79,300,000⏳باحواله🔴فروش\nگرم: 18,306,477",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].settlement_term, "TOMORROW")
        self.assertEqual(events[0].event_type, "OFFER")
        self.assertEqual(events[0].side, "SELL")

    def test_naghdp_trade_requires_context_for_side(self) -> None:
        events = parse_message(
            "NaghdP",
            "79,270,000⏳باحواله✅معامله\nگرم: 18,299,552",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "TRADE")
        self.assertEqual(events[0].side, "UNKNOWN")

    def test_naghdp_gram_only_message_is_ignored(self) -> None:
        self.assertEqual(parse_message("NaghdP", "گرم: 18,299,552"), [])

    def test_abshdh_classifies_cash_as_physical_without_inferring_today(self) -> None:
        events = parse_message("abshdh", "🔻#آبشده_‌نقدی 79,000,000")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].trade_form, "PHYSICAL")
        self.assertEqual(events[0].settlement_term, "UNKNOWN")

    def test_abshdh_classifies_official_as_physical_without_inferring_today(self) -> None:
        events = parse_message("abshdh", "🔻#آبشده_‌رسمی 79,100,000")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].market_label, "آبشده رسمی")
        self.assertEqual(events[0].trade_form, "PHYSICAL")
        self.assertEqual(events[0].settlement_term, "UNKNOWN")

    def test_abshdh_today_without_cash_or_official_is_paper(self) -> None:
        events = parse_message("abshdh", "🔻#آبشده_‌امروزی 79,200,000")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].trade_form, "PAPER")
        self.assertEqual(events[0].settlement_term, "TODAY")

    def test_abshdh_extracts_multiline_union_quote(self) -> None:
        events = parse_message(
            "abshdh",
            "#مظنه‌اتحادیه:\n🔺 78,700,000 🔺\nJust In Time 12:38:00",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].instrument, "GOLD_UNION_QUOTE")
        self.assertEqual(events[0].price, Decimal("78700000"))
        self.assertEqual(events[0].movement, "UP")

    def test_abshdh_ignores_melted_gold_hourly_pivot(self) -> None:
        events = parse_message(
            "abshdh",
            """
            🔹پیوت #آبشده‌‌‌نقدی حواله
            🔹#مرورنوسانات یک ساعت اخیر :
            سقف: 79,650,000 :High
            شروع: 79,600,000 :Start
            کف: 78,950,000 :Low
            Just In Time 12:00:02
            """,
        )

        self.assertEqual(events, [])
        self.assertTrue(should_ignore_message("abshdh", "پیوت #آبشده\n#مرورنوسانات"))

    def test_abshdh_does_not_parse_promotional_price_mentions(self) -> None:
        events = parse_message(
            "abshdh",
            "فعلا 86200ابشده هدفش بعدی 90میلیون\nهدف دلار 200هزار فعال شده",
        )
        self.assertEqual(events, [])

    def test_abshdh_classifies_global_melted_gold(self) -> None:
        events = parse_message(
            "abshdh",
            "🔺 #آبشده‌جهانی: 79,571,072 با درهم\nJust In Time 12:32:18",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].instrument, "MELTED_GOLD")
        self.assertEqual(events[0].market_label, "آبشده جهانی")
        self.assertEqual(events[0].price, Decimal("79571072"))

    def test_abshdh_extracts_coin_and_dirham_quotes(self) -> None:
        cash_coin = parse_message(
            "abshdh",
            "#سکه‌نقدی: 🔺 182,200,000 🔺\nJust In Time 11:15:48",
        )
        transfer_coin = parse_message(
            "abshdh",
            "#سکه‌حواله: 🔻 184,500,000 🔻\nJust In Time 12:06:07",
        )
        dirham = parse_message(
            "abshdh",
            "🔻#درهم‌دبی: 51,600 🇦🇪\nJust In Time 12:32:18",
        )

        self.assertEqual(cash_coin[0].instrument, "GOLD_COIN")
        self.assertEqual(cash_coin[0].trade_form, "PHYSICAL")
        self.assertEqual(cash_coin[0].settlement_term, "UNKNOWN")
        self.assertEqual(cash_coin[0].price, Decimal("182200000"))
        self.assertEqual(transfer_coin[0].market_label, "سکه حواله")
        self.assertEqual(transfer_coin[0].trade_form, "PAPER")
        self.assertEqual(transfer_coin[0].settlement_term, "UNKNOWN")
        self.assertEqual(dirham[0].instrument, "AED_DUBAI")
        self.assertEqual(dirham[0].price, Decimal("51600"))

    def test_coin_form_and_settlement_are_independent(self) -> None:
        today = parse_message(
            "abshdh",
            "#سکه نقدی امروز: 182,200,000",
        )[0]
        tomorrow = parse_message(
            "abshdh",
            "#سکه حواله فردا: 184,500,000",
        )[0]

        self.assertEqual((today.trade_form, today.settlement_term), ("PHYSICAL", "TODAY"))
        self.assertEqual((tomorrow.trade_form, tomorrow.settlement_term), ("PAPER", "TOMORROW"))

    def test_abshdh_ignores_coin_hourly_pivot(self) -> None:
        events = parse_message(
            "abshdh",
            """
            🔹پیوت #سکه‌حواله
            🔹#مرورنوسانات یک ساعت اخیر :
            سقف: 185,400,000 :High
            شروع: 185,000,000 :Start
            کف: 184,000,000 :Low
            Just In Time 12:00:02
            """,
        )

        self.assertEqual(events, [])
        self.assertTrue(should_ignore_message("abshdh", "پیوت #سکه\n#مرورنوسانات"))

    def test_ounce_extracts_decimal_and_jalali_timestamp(self) -> None:
        events = parse_message(
            "qheimat_ounce",
            "🔴4538.39 [1405-02-26 00:24:35]",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].instrument, "XAUUSD")
        self.assertEqual(events[0].price, Decimal("4538.39"))
        self.assertEqual(events[0].movement, "DOWN")
        self.assertEqual(events[0].source_datetime_text, "1405-02-26 00:24:35")

    def test_dollar_offer_classification(self) -> None:
        events = parse_message(
            "ToofanHarirodOfficial",
            "هرات فردایی ⏳ 114,500 خــرید🔵",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].settlement_term, "TOMORROW")
        self.assertEqual(events[0].event_type, "OFFER")
        self.assertEqual(events[0].side, "BUY")
        self.assertEqual(events[0].trade_form, "PAPER")
        self.assertEqual(events[0].price, Decimal("114500"))

    def test_only_explicit_cash_dollar_is_physical_not_implicitly_today(self) -> None:
        events = parse_message(
            "ToofanHarirodOfficial",
            "هرات نقدی 114,300 فروش",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].settlement_term, "UNKNOWN")
        self.assertEqual(events[0].trade_form, "PHYSICAL")
        self.assertEqual(events[0].event_type, "OFFER")
        self.assertEqual(events[0].side, "SELL")

    def test_dollar_today_without_cash_marker_is_paper(self) -> None:
        events = parse_message(
            "ToofanHarirodOfficial",
            "هرات امروز 114,400 خرید",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].settlement_term, "TODAY")
        self.assertEqual(events[0].trade_form, "PAPER")

    def test_dollar_keeps_paper_and_settlement_as_independent_dimensions(self) -> None:
        events = parse_message(
            "ToofanHarirodOfficial",
            "هرات امروز کاغذی 114,500 فروش",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].settlement_term, "TODAY")
        self.assertEqual(events[0].trade_form, "PAPER")
        self.assertEqual(events[0].event_type, "OFFER")
        self.assertEqual(events[0].side, "SELL")

    def test_dollar_completed_trade_and_quantity(self) -> None:
        events = parse_message(
            "ToofanHarirodOfficial",
            "104,200 معامله ⏳ فردایی ✔️ 3میلیارد",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "TRADE")
        self.assertEqual(events[0].quantity, Decimal("3"))
        self.assertEqual(events[0].quantity_unit, "BILLION_TOMAN")

    def test_ads_are_not_parsed_as_prices(self) -> None:
        events = parse_message(
            "ToofanHarirodOfficial",
            "برای عضویت در کانال تحلیل بازار با 20 درصد تخفیف کلیک کنید",
        )
        self.assertEqual(events, [])

    def test_dollar_market_summary_is_ignored(self) -> None:
        text = "شروع معاملات 189,300\nسقف معاملات 189,400\nآخرین معامله 187,000"
        self.assertTrue(should_ignore_message("ToofanHarirodOfficial", text))
        self.assertEqual(parse_message("ToofanHarirodOfficial", text), [])


if __name__ == "__main__":
    unittest.main()
