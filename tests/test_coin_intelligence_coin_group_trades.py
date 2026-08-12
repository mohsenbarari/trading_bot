"""Reply-chain tests for confirmed coin-group trades."""

from __future__ import annotations

import unittest

from core.market_intelligence.coin_group_resolution import ResolvedCoinGroupOffer
from core.market_intelligence.coin_group_staging import StagedCoinGroupMessage
from core.market_intelligence.coin_group_trades import (
    CoinGroupOfferRecord,
    coin_group_trade_observations,
    link_coin_group_trades,
)


OWNER = b"o" * 32
BUYER_ONE = b"b" * 32
BUYER_TWO = b"c" * 32


def message(message_id: int, sender: bytes, text: str, reply: int | None = None, *, at_second: int = 0) -> StagedCoinGroupMessage:
    return StagedCoinGroupMessage(
        group_number=1,
        message_id=message_id,
        event_time_utc=f"2026-08-04T10:00:{at_second:02d}Z",
        available_at_utc=f"2026-08-04T10:00:{at_second + 1:02d}Z",
        text=text,
        reply_to_message_id=reply,
        sender_digest=sender,
        edited_at_utc=None,
        revision=1,
        expires_at_utc="2026-08-07T10:00:00Z",
    )


def offer_record(*, price: int = 183_100, quantity: int = 10) -> CoinGroupOfferRecord:
    offer = ResolvedCoinGroupOffer(
        offer_index=0,
        commodity_code="IMAM",
        price_project_thousand_toman=price,
        quantity=quantity,
        side="SELL",
        settlement_term="TOMORROW",
        trade_form="PHYSICAL",
        is_conditional=False,
        quality_state="ELIGIBLE",
        resolution_reason="test",
        anchor_count=2,
        relative_distance=0.001,
    )
    return CoinGroupOfferRecord(1, 1, OWNER, "2026-08-04T10:00:00Z", "2026-08-04T10:00:01Z", offer)


class CoinGroupTradeTests(unittest.TestCase):
    def test_negotiated_reply_price_requires_offer_owner_confirmation(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "ب10 تا182900", reply=1, at_second=2),
            message(3, OWNER, "برکت", reply=2, at_second=4),
        ]
        trades = link_coin_group_trades(rows, [offer_record()])
        self.assertEqual(len(trades), 1)
        self.assertEqual((trades[0].price_project_thousand_toman, trades[0].quantity, trades[0].confirmation_kind), (182_900, 10, "RECIPROCAL_OFFERER_CONFIRMATION"))

    def test_quantity_question_then_owner_acceptance_is_a_partial_fill(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "3 تا میشه؟", reply=1, at_second=2),
            message(3, OWNER, "چشم ب", reply=2, at_second=4),
        ]
        trades = link_coin_group_trades(rows, [offer_record()])
        self.assertEqual([(item.quantity, item.price_project_thousand_toman) for item in trades], [(3, 183_100)])

    def test_multiple_confirmed_partial_branches_stay_separate_and_bounded(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "3 تا میشه؟", reply=1, at_second=2),
            message(3, OWNER, "ب", reply=2, at_second=4),
            message(4, BUYER_TWO, "5 تا مال من", reply=1, at_second=5),
            message(5, OWNER, "برکت", reply=4, at_second=7),
            message(6, BUYER_TWO, "5 تا مال من", reply=1, at_second=8),
            message(7, OWNER, "برکت", reply=6, at_second=9),
        ]
        trades = link_coin_group_trades(rows, [offer_record()])
        self.assertEqual([item.quantity for item in trades], [3, 5])

    def test_unconfirmed_buy_request_is_not_a_trade(self) -> None:
        rows = [message(1, OWNER, "10 تا ف 183100"), message(2, BUYER_ONE, "ب10 تا182900", reply=1, at_second=2)]
        self.assertEqual(link_coin_group_trades(rows, [offer_record()]), [])

    def test_missing_transient_identity_cannot_confirm_a_trade(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100"),
            message(2, BUYER_ONE, "ب10 تا182900", reply=1, at_second=2),
            message(3, None, "برکت", reply=2, at_second=4),  # type: ignore[arg-type]
        ]
        self.assertEqual(link_coin_group_trades(rows, [offer_record()]), [])

    def test_cumulative_owner_declaration_is_recorded_but_not_model_eligible_if_over_offer(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100", at_second=0),
            message(2, OWNER, "کلاً 28 تا خریدم", reply=1, at_second=5),
        ]
        trades = link_coin_group_trades(rows, [offer_record()])
        self.assertEqual(len(trades), 1)
        self.assertEqual((trades[0].quantity, trades[0].quality_state, trades[0].is_aggregate), (28, "PENDING_REVIEW", True))
        observation = coin_group_trade_observations(trades)[0].normalized()
        self.assertNotIn("message", observation.attributes_json)
        self.assertNotIn("sender", observation.attributes_json)


if __name__ == "__main__":
    unittest.main()
