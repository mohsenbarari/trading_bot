from __future__ import annotations

import unittest

from core.market_intelligence.group_trade_parser import (
    analyze_reply_trades,
    classify_signal,
)


def message(
    message_id: int,
    sender: str,
    text: str,
    reply_to: int | None = None,
) -> dict:
    return {
        "message_id": message_id,
        "date_utc": f"2026-08-03T07:{message_id:02d}:00Z",
        "from_name": sender,
        "text": text,
        "reply_to_message_id": reply_to,
    }


def offer(*, price: int = 183_100, quantity: int = 10) -> dict:
    return {
        "commodity": "امام",
        "price": price,
        "price_raw": str(price),
        "price_method": "full",
        "quantity": quantity,
        "quantity_method": "explicit",
        "side": "SELL",
        "settlement": "TOMORROW",
        "trade_form": "PHYSICAL",
        "confidence": 0.99,
        "commodity_method": "default_imam_omitted_commodity",
    }


class GroupTradeParserTests(unittest.TestCase):
    def test_price_qualified_buy_proposal_uses_negotiated_price(self) -> None:
        messages = [
            message(1, "offerer", "10 تا ف 183100"),
            message(2, "buyer", "ب10 تا182900", reply_to=1),
            message(3, "offerer", "برکت", reply_to=2),
        ]

        analysis = analyze_reply_trades(messages, {1: [offer()]})

        self.assertEqual(classify_signal("ب10 تا182900"), "BUY_ACCEPT")
        self.assertEqual(len(analysis["accepted_trades"]), 1)
        trade = analysis["accepted_trades"][0]
        self.assertEqual(trade["price"], 182_900)
        self.assertEqual(trade["quantity"], 10)
        self.assertEqual(trade["confirmation_type"], "RECIPROCAL_OFFERER_CONFIRMATION")
        self.assertEqual(trade["price_method"], "full")

    def test_price_before_buy_marker_is_a_buy_proposal(self) -> None:
        messages = [
            message(1, "offerer", "10 تا نیم 93200 ف"),
            message(2, "buyer", "93100 ب", reply_to=1),
            message(3, "offerer", "ب", reply_to=2),
        ]
        half = {**offer(price=93_200), "commodity": "نیم بهار"}

        analysis = analyze_reply_trades(messages, {1: [half]})

        self.assertEqual(classify_signal("93100 ب"), "BUY_ACCEPT")
        self.assertEqual(len(analysis["accepted_trades"]), 1)
        self.assertEqual(analysis["accepted_trades"][0]["price"], 93_100)

    def test_offerers_cheshm_b_confirms_a_quantity_request(self) -> None:
        messages = [
            message(1, "offerer", "10 تا 182300 خ"),
            message(2, "buyer", "3 تا میشه؟", reply_to=1),
            message(3, "offerer", "چشم ب", reply_to=2),
        ]

        analysis = analyze_reply_trades(messages, {1: [offer(price=182_300)]})

        self.assertEqual(classify_signal("چشم ب"), "ACCEPT")
        self.assertEqual(len(analysis["accepted_trades"]), 1)
        trade = analysis["accepted_trades"][0]
        self.assertEqual(trade["price"], 182_300)
        self.assertEqual(trade["quantity"], 3)
        self.assertEqual(trade["confirmation_type"], "RECIPROCAL_OFFERER_CONFIRMATION")


if __name__ == "__main__":
    unittest.main()
