"""Reply-chain tests for confirmed coin-group trades."""

from __future__ import annotations

import json
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


def offer_record(
    *,
    message_id: int = 1,
    owner: bytes = OWNER,
    price: int = 183_100,
    quantity: int = 10,
    commodity: str | None = "IMAM",
    quality: str = "ELIGIBLE",
) -> CoinGroupOfferRecord:
    offer = ResolvedCoinGroupOffer(
        offer_index=0,
        commodity_code=commodity,
        price_project_thousand_toman=price,
        quantity=quantity,
        side="SELL",
        settlement_term="TOMORROW",
        trade_form="PHYSICAL",
        is_conditional=False,
        quality_state=quality,
        resolution_reason="test",
        anchor_count=2,
        relative_distance=0.001,
    )
    return CoinGroupOfferRecord(
        1,
        message_id,
        owner,
        "2026-08-04T10:00:00Z",
        "2026-08-04T10:00:01Z",
        offer,
    )


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

    def test_owner_accepts_explicit_quantity_above_root_as_amended_first_fill(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف ن 187500", at_second=0),
            message(2, BUYER_ONE, "15تا؟", reply=1, at_second=2),
            message(3, OWNER, "برکت", reply=2, at_second=4),
        ]
        trades = link_coin_group_trades(
            rows,
            [offer_record(price=187_500, quantity=10)],
        )
        self.assertEqual(len(trades), 1)
        self.assertEqual(
            (
                trades[0].quantity,
                trades[0].quality_state,
                trades[0].quantity_was_negotiated,
                trades[0].resolution_reason,
            ),
            (15, "ELIGIBLE", True, "STRUCTURALLY_LINKED_CONFIRMED_TRADE"),
        )

    def test_counterparty_accepts_owner_quantity_above_root_as_amended_first_fill(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "15تا؟", reply=1, at_second=2),
            message(3, OWNER, "15 تا", reply=2, at_second=3),
            message(4, BUYER_ONE, "ب", reply=3, at_second=4),
        ]
        trades = link_coin_group_trades(rows, [offer_record(quantity=10)])
        self.assertEqual(len(trades), 1)
        self.assertEqual(
            (
                trades[0].quantity,
                trades[0].quality_state,
                trades[0].quantity_was_negotiated,
                trades[0].confirmation_kind,
            ),
            (15, "ELIGIBLE", True, "RECIPROCAL_COUNTERPARTY_CONFIRMATION"),
        )

    def test_amended_first_fill_does_not_open_capacity_for_later_branches(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "15تا؟", reply=1, at_second=2),
            message(3, OWNER, "برکت", reply=2, at_second=3),
            message(4, BUYER_TWO, "1 تا من", reply=1, at_second=4),
            message(5, OWNER, "برکت", reply=4, at_second=5),
        ]
        trades = link_coin_group_trades(rows, [offer_record(quantity=10)])
        self.assertEqual(
            [(item.quantity, item.quality_state) for item in trades],
            [(15, "ELIGIBLE"), (1, "PENDING_REVIEW")],
        )
        self.assertEqual(
            trades[-1].resolution_reason,
            "NON_AGGREGATE_FILL_EXCEEDS_REMAINING_ROOT_QUANTITY",
        )

    def test_final_negotiated_price_and_quantity_come_from_the_full_reply_branch(self) -> None:
        rows = [
            message(1, OWNER, "20 تا ف 177100", at_second=0),
            message(2, BUYER_ONE, "300", reply=1, at_second=2),
            message(3, BUYER_ONE, "ب 10", reply=2, at_second=3),
            message(4, OWNER, "ب", reply=3, at_second=4),
        ]
        trades = link_coin_group_trades(rows, [offer_record(price=177_100, quantity=20)])
        self.assertEqual(
            [(item.price_project_thousand_toman, item.quantity) for item in trades],
            [(177_300, 10)],
        )

    def test_bare_price_tails_are_not_misclassified_as_quantities(self) -> None:
        cases = (
            (215_300, 20, "100 ب", 215_100, 20),
            (217_000, 10, "100 ب", 217_100, 10),
            (54_900, 9, "55", 55_000, 9),
        )
        for root_price, root_quantity, reply, final_price, final_quantity in cases:
            with self.subTest(reply=reply, root_price=root_price):
                rows = [
                    message(1, OWNER, "root", at_second=0),
                    message(2, BUYER_ONE, reply, reply=1, at_second=2),
                    message(3, OWNER, "برکت", reply=2, at_second=4),
                ]
                commodity = "QUARTER_BAHAR" if root_price < 100_000 else "IMAM"
                trades = link_coin_group_trades(
                    rows,
                    [
                        offer_record(
                            price=root_price,
                            quantity=root_quantity,
                            commodity=commodity,
                        )
                    ],
                )
                self.assertEqual(
                    [(item.price_project_thousand_toman, item.quantity) for item in trades],
                    [(final_price, final_quantity)],
                )

        explicit_hundred = [
            message(1, OWNER, "root", at_second=0),
            message(2, BUYER_ONE, "100 تا", reply=1, at_second=2),
            message(3, OWNER, "برکت", reply=2, at_second=4),
        ]
        self.assertEqual(
            link_coin_group_trades(
                explicit_hundred,
                [offer_record(quantity=20)],
            )[0].quantity,
            100,
        )

    def test_unambiguous_direct_siblings_form_one_reciprocal_trade(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "3 تا 182900 ب", reply=1, at_second=2),
            message(3, OWNER, "برکت", reply=1, at_second=4),
        ]
        trades = link_coin_group_trades(rows, [offer_record()])
        self.assertEqual(len(trades), 1)
        self.assertEqual(
            (
                trades[0].price_project_thousand_toman,
                trades[0].quantity,
                trades[0].confirmation_kind,
            ),
            (182_900, 3, "SIBLING_RECIPROCAL_OFFERER_CONFIRMATION"),
        )

    def test_multiple_counterparty_siblings_remain_ambiguous(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "3 تا 182900 ب", reply=1, at_second=2),
            message(3, BUYER_TWO, "4 تا 182800 ب", reply=1, at_second=3),
            message(4, OWNER, "برکت", reply=1, at_second=4),
        ]
        self.assertEqual(link_coin_group_trades(rows, [offer_record()]), [])

    def test_dot_or_slash_negotiated_price_is_not_truncated(self) -> None:
        for reply, expected in (("ب 5 تا 182.900", 182_900), ("ب 5 تا 182/800", 182_800)):
            with self.subTest(reply=reply):
                rows = [
                    message(1, OWNER, "10 تا ف 183100", at_second=0),
                    message(2, BUYER_ONE, reply, reply=1, at_second=2),
                    message(3, OWNER, "برکت", reply=2, at_second=4),
                ]
                trades = link_coin_group_trades(rows, [offer_record()])
                self.assertEqual(len(trades), 1)
                self.assertEqual(trades[0].price_project_thousand_toman, expected)

    def test_full_toman_and_redundant_zero_negotiated_prices_are_exact(self) -> None:
        cases = (
            ("5 تا 188.500.000", offer_record(price=188_750), 188_500),
            ("5 تا 188500000", offer_record(price=188_750), 188_500),
            (
                "5 تا 519000",
                offer_record(price=51_500, commodity="QUARTER_BAHAR"),
                51_900,
            ),
        )
        for reply, root, expected in cases:
            with self.subTest(reply=reply):
                rows = [
                    message(1, OWNER, "10 تا ف 188750", at_second=0),
                    message(2, BUYER_ONE, reply, reply=1, at_second=2),
                    message(3, OWNER, "برکت", reply=2, at_second=4),
                ]
                trades = link_coin_group_trades(rows, [root])
                self.assertEqual(len(trades), 1)
                self.assertEqual(trades[0].price_project_thousand_toman, expected)

    def test_low_date_two_digit_negotiated_price_uses_instrument_band(self) -> None:
        rows = [
            message(1, OWNER, "10 تا رب پ ف 47000", at_second=0),
            message(2, BUYER_ONE, "5 تا 48", reply=1, at_second=2),
            message(3, OWNER, "قبوله", reply=2, at_second=4),
        ]
        trades = link_coin_group_trades(
            rows,
            [offer_record(price=47_000, commodity="QUARTER_LOW_DATE")],
        )
        self.assertEqual(
            [(item.price_project_thousand_toman, item.quantity) for item in trades],
            [(48_000, 5)],
        )

    def test_large_but_plausible_negotiated_price_is_retained_and_gated(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "5 تا 200000", reply=1, at_second=2),
            message(3, OWNER, "برکت", reply=2, at_second=4),
        ]
        trades = link_coin_group_trades(rows, [offer_record()])
        self.assertEqual(len(trades), 1)
        self.assertEqual(
            (
                trades[0].price_project_thousand_toman,
                trades[0].quantity,
                trades[0].quality_state,
                trades[0].resolution_reason,
            ),
            (
                200_000,
                5,
                "PENDING_REVIEW",
                "NEGOTIATED_PRICE_OUTSIDE_SAFE_RELATIVE_DELTA",
            ),
        )

    def test_bare_numeric_quantity_and_price_are_both_retained(self) -> None:
        rows = [
            message(1, OWNER, "20 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "10 182900", reply=1, at_second=2),
            message(3, OWNER, "برکت", reply=2, at_second=4),
        ]
        trades = link_coin_group_trades(
            rows,
            [offer_record(price=183_100, quantity=20)],
        )
        self.assertEqual(
            [(item.price_project_thousand_toman, item.quantity) for item in trades],
            [(182_900, 10)],
        )

    def test_sell_shorthand_quantity_is_valid_counterparty_proposal(self) -> None:
        rows = [
            message(1, OWNER, "20 تا خ 183100", at_second=0),
            message(2, BUYER_ONE, "ف 4 182900", reply=1, at_second=2),
            message(3, OWNER, "تأیید", reply=2, at_second=4),
        ]
        trades = link_coin_group_trades(
            rows,
            [offer_record(price=183_100, quantity=20)],
        )
        self.assertEqual(
            [(item.price_project_thousand_toman, item.quantity) for item in trades],
            [(182_900, 4)],
        )

    def test_latest_counterparty_quantity_overrides_an_earlier_owner_counter(self) -> None:
        rows = [
            message(1, OWNER, "35 تا ف 183100", at_second=0),
            message(2, OWNER, "25", reply=1, at_second=2),
            message(3, BUYER_ONE, "9ب", reply=2, at_second=3),
            message(4, OWNER, "ب", reply=3, at_second=4),
        ]
        trades = link_coin_group_trades(rows, [offer_record(quantity=35)])
        self.assertEqual([item.quantity for item in trades], [9])

    def test_offer_shaped_counter_reply_cannot_steal_original_root(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "5 تا خ 182900", reply=1, at_second=2),
            message(3, OWNER, "برکت", reply=2, at_second=4),
        ]
        counter_offer = offer_record(
            message_id=2,
            owner=BUYER_ONE,
            price=182_900,
            quantity=5,
            commodity=None,
            quality="PENDING_REVIEW",
        )
        trades = link_coin_group_trades(rows, [offer_record(), counter_offer])
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].root_offer_message_id, 1)
        self.assertEqual(trades[0].quantity, 5)

    def test_multi_user_negotiation_uses_only_the_confirmed_reply_path(self) -> None:
        rows = [
            message(1, OWNER, "20 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "3 تا 182900", reply=1, at_second=2),
            message(3, BUYER_TWO, "5 تا 182700", reply=1, at_second=3),
            message(4, OWNER, "برکت", reply=2, at_second=4),
        ]
        trades = link_coin_group_trades(rows, [offer_record(quantity=20)])
        self.assertEqual(
            [(item.price_project_thousand_toman, item.quantity) for item in trades],
            [(182_900, 3)],
        )

    def test_counterparty_can_confirm_an_owner_counter_only_after_prior_participation(self) -> None:
        valid = [
            message(1, OWNER, "20 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "182900", reply=1, at_second=2),
            message(3, OWNER, "182800", reply=2, at_second=3),
            message(4, BUYER_ONE, "ب", reply=3, at_second=4),
        ]
        trades = link_coin_group_trades(valid, [offer_record(quantity=20)])
        self.assertEqual(
            [(item.price_project_thousand_toman, item.confirmation_kind) for item in trades],
            [(182_800, "RECIPROCAL_COUNTERPARTY_CONFIRMATION")],
        )

        third_party_jump = valid[:-1] + [
            message(4, BUYER_TWO, "ب", reply=3, at_second=4)
        ]
        self.assertEqual(
            link_coin_group_trades(third_party_jump, [offer_record(quantity=20)]),
            [],
        )

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
        self.assertEqual([item.quantity for item in trades], [3, 5, 5])
        self.assertEqual(
            [item.quality_state for item in trades],
            ["ELIGIBLE", "ELIGIBLE", "PENDING_REVIEW"],
        )
        self.assertEqual(
            trades[-1].resolution_reason,
            "NON_AGGREGATE_FILL_EXCEEDS_REMAINING_ROOT_QUANTITY",
        )

    def test_unconfirmed_buy_request_is_not_a_trade(self) -> None:
        rows = [message(1, OWNER, "10 تا ف 183100"), message(2, BUYER_ONE, "ب10 تا182900", reply=1, at_second=2)]
        self.assertEqual(link_coin_group_trades(rows, [offer_record()]), [])

    def test_counterparty_only_trade_declaration_is_audit_only(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "5 تا خریدم", reply=1, at_second=2),
        ]
        trades = link_coin_group_trades(rows, [offer_record()])
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].quality_state, "PENDING_REVIEW")
        self.assertEqual(
            trades[0].resolution_reason,
            "COUNTERPARTY_DECLARATION_REQUIRES_OFFERER_CONFIRMATION",
        )

    def test_rejection_breaks_old_terms_until_a_new_proposal_exists(self) -> None:
        rejected = [
            message(1, OWNER, "10 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "5 تا 182900", reply=1, at_second=2),
            message(3, OWNER, "نشد", reply=2, at_second=3),
            message(4, BUYER_ONE, "ب", reply=3, at_second=4),
        ]
        self.assertEqual(link_coin_group_trades(rejected, [offer_record()]), [])

        reopened = rejected[:-1] + [
            message(4, OWNER, "5 تا 182950", reply=3, at_second=4),
            message(5, BUYER_ONE, "ب", reply=4, at_second=5),
        ]
        trades = link_coin_group_trades(reopened, [offer_record()])
        self.assertEqual(
            [(item.price_project_thousand_toman, item.quantity) for item in trades],
            [(182_950, 5)],
        )

    def test_participant_cancellation_after_confirmation_gates_the_trade(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "5 تا خریدم", reply=1, at_second=2),
            message(3, OWNER, "برکت", reply=2, at_second=3),
            message(4, BUYER_ONE, "لغو شد", reply=3, at_second=4),
        ]
        trades = link_coin_group_trades(rows, [offer_record()])
        self.assertEqual(len(trades), 1)
        self.assertEqual(
            (trades[0].quality_state, trades[0].resolution_reason),
            ("PENDING_REVIEW", "PARTICIPANT_REJECTION_AFTER_CONFIRMATION"),
        )

    def test_missing_ancestry_cannot_promote_a_retained_counter_offer_to_root(self) -> None:
        rows = [
            message(2, BUYER_ONE, "5 تا خ 182900", reply=99, at_second=2),
            message(3, OWNER, "خریدم", reply=2, at_second=4),
        ]
        counter_offer = offer_record(
            message_id=2,
            owner=BUYER_ONE,
            price=182_900,
            quantity=5,
        )
        self.assertEqual(link_coin_group_trades(rows, [counter_offer]), [])

    def test_counterparty_declaration_and_owner_confirmation_are_one_fill(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "5 تا خریدم", reply=1, at_second=2),
            message(3, OWNER, "برکت", reply=2, at_second=4),
        ]
        trades = link_coin_group_trades(rows, [offer_record()])
        self.assertEqual(len(trades), 1)
        self.assertEqual(
            (trades[0].quantity, trades[0].confirmation_kind),
            (5, "RECIPROCAL_OFFERER_CONFIRMATION"),
        )

    def test_missing_transient_identity_cannot_confirm_a_trade(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100"),
            message(2, BUYER_ONE, "ب10 تا182900", reply=1, at_second=2),
            message(3, None, "برکت", reply=2, at_second=4),  # type: ignore[arg-type]
        ]
        self.assertEqual(link_coin_group_trades(rows, [offer_record()]), [])

    def test_confirmed_branch_on_unresolved_root_is_retained_but_not_model_eligible(self) -> None:
        rows = [
            message(1, OWNER, "10 تا ف 183100", at_second=0),
            message(2, BUYER_ONE, "5 تا خریدم", reply=1, at_second=2),
            message(3, OWNER, "برکت", reply=2, at_second=4),
        ]
        trades = link_coin_group_trades(
            rows,
            [offer_record(commodity=None, quality="PENDING_REVIEW")],
        )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].quality_state, "PENDING_REVIEW")
        self.assertIsNone(trades[0].commodity_code)
        observation = coin_group_trade_observations(trades)[0].normalized()
        self.assertEqual(observation.instrument, "COIN_UNRESOLVED")

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
        evidence = json.loads(observation.attributes_json)["field_evidence"]
        self.assertEqual(
            evidence["event_type"],
            ["OWNER_EXPLICIT_AGGREGATE_REPLY_TRADE"],
        )
        self.assertEqual(
            evidence["price"],
            ["EXACT_REPLY_BRANCH_LAST_AGREED_TERM"],
        )


if __name__ == "__main__":
    unittest.main()
