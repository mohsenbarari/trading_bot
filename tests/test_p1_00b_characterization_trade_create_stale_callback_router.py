"""Characterization tests for stale trade creation callback router dispatch.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Exact seeds characterized:
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3504
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3505
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3506
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3507
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3508
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3509
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3510
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3511
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3512
- BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3513
- BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3514
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3515
- BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3516

This partial characterization unit executes registered router dispatch under live
aiogram filter rules. These assertions do not close any seed, blocker
(P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE), reconciliation, or Stage P1-00b.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest import mock

from aiogram import types as aiogram_types

from bot.callbacks import (
    AcceptLotsCallback,
    CommodityCallback,
    LotTypeCallback,
    PageCallback,
    QuantityCallback,
    SkipNotesCallback,
    TextOfferActionCallback,
    TextOfferInferenceCandidateCallback,
    TradeActionCallback,
    TradeSettlementCallback,
    TradeTypeCallback,
    TradeWizardActionCallback,
    TradeWizardEditCallback,
)
from bot.handlers import trade_create
from bot.handlers.trade_create import (
    STALE_TRADE_CREATION_CALLBACK_TEXT,
)

CALLBACK_PAYLOAD_SPECS = (
    (
        "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3504",
        TradeTypeCallback(type="buy"),
        "trade_type",
    ),
    (
        "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3505",
        TradeSettlementCallback(type="cash"),
        "trade_settlement",
    ),
    (
        "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3506",
        CommodityCallback(id=1),
        "trade_commodity",
    ),
    (
        "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3507",
        PageCallback(trade_type="buy", page=1),
        "trade_page",
    ),
    (
        "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3508",
        QuantityCallback(value="10"),
        "quantity",
    ),
    (
        "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3509",
        LotTypeCallback(type="wholesale"),
        "lot_type",
    ),
    (
        "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3510",
        AcceptLotsCallback(lots="10_10"),
        "accept_lots",
    ),
    (
        "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3511",
        TradeActionCallback(action="back_to_type"),
        "trade",
    ),
    (
        "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3512",
        SkipNotesCallback(target="notes"),
        "skip",
    ),
    (
        "BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3513",
        TextOfferActionCallback(action="confirm"),
        "text_offer",
    ),
    (
        "BOT-OFFER-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3514",
        TextOfferInferenceCandidateCallback(commodity_id=73),
        "text_offer_coin",
    ),
    (
        "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3515",
        TradeWizardActionCallback(action="continue"),
        "trade_wizard",
    ),
    (
        "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3516",
        TradeWizardEditCallback(field="commodity"),
        "trade_edit",
    ),
)


class TestTradeCreateStaleCallbackRouterCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    async def test_stale_callback_router_unauthenticated_scenario(self):
        sentinel_time = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)

        for seed_id, payload, expected_prefix in CALLBACK_PAYLOAD_SPECS:
            with self.subTest(seed_id=seed_id, callback_prefix=expected_prefix):
                packed_data = payload.pack()
                event = aiogram_types.CallbackQuery(
                    id=f"cb-unauth-{expected_prefix}",
                    from_user=aiogram_types.User(
                        id=999,
                        is_bot=False,
                        first_name="UnauthUser",
                    ),
                    chat_instance="test_chat_instance",
                    data=packed_data,
                )

                mock_state = SimpleNamespace(
                    get_state=mock.AsyncMock(),
                    set_state=mock.AsyncMock(),
                    clear=mock.AsyncMock(),
                    update_data=mock.AsyncMock(),
                )

                with (
                    mock.patch(
                        "bot.handlers.trade_create.utc_now",
                        return_value=sentinel_time,
                    ) as mock_utc_now,
                    mock.patch(
                        "bot.handlers.trade_create._answer_stale_trade_creation_callback",
                        new_callable=mock.AsyncMock,
                    ) as mock_answer,
                    mock.patch.object(
                        aiogram_types.CallbackQuery,
                        "answer",
                        new_callable=mock.AsyncMock,
                    ) as mock_callback_answer,
                ):
                    result = await trade_create.router.propagate_event(
                        "callback_query",
                        event,
                        state=mock_state,
                        user=None,
                        raw_state=None,
                    )

                self.assertIsNone(result)
                mock_utc_now.assert_called_once_with()
                mock_answer.assert_awaited_once_with(
                    event,
                    received_at=sentinel_time,
                )
                self.assertEqual(mock_answer.await_args.args, (event,))
                self.assertIs(mock_answer.await_args.args[0], event)
                self.assertEqual(
                    mock_answer.await_args.kwargs,
                    {"received_at": sentinel_time},
                )
                self.assertNotIn("text", mock_answer.await_args.kwargs)
                self.assertNotIn("show_alert", mock_answer.await_args.kwargs)

                mock_state.get_state.assert_not_called()
                mock_state.set_state.assert_not_called()
                mock_state.clear.assert_not_called()
                mock_state.update_data.assert_not_called()
                mock_callback_answer.assert_not_called()

    async def test_stale_callback_router_authenticated_scenario(self):
        sentinel_time = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        fixed_trade_state = "trade:awaiting_trade_type"
        runtime_user = SimpleNamespace(id=276)

        for seed_id, payload, expected_prefix in CALLBACK_PAYLOAD_SPECS:
            with self.subTest(seed_id=seed_id, callback_prefix=expected_prefix):
                packed_data = payload.pack()
                event = aiogram_types.CallbackQuery(
                    id=f"cb-auth-{expected_prefix}",
                    from_user=aiogram_types.User(
                        id=runtime_user.id,
                        is_bot=False,
                        first_name="AuthUser",
                    ),
                    chat_instance="test_chat_instance",
                    data=packed_data,
                )

                mock_state = SimpleNamespace(
                    get_state=mock.AsyncMock(return_value=fixed_trade_state),
                    set_state=mock.AsyncMock(),
                    clear=mock.AsyncMock(),
                    update_data=mock.AsyncMock(),
                )

                with (
                    mock.patch(
                        "bot.handlers.trade_create.utc_now",
                        return_value=sentinel_time,
                    ) as mock_utc_now,
                    mock.patch(
                        "bot.handlers.trade_create._answer_stale_trade_creation_callback",
                        new_callable=mock.AsyncMock,
                    ) as mock_answer,
                    mock.patch(
                        "bot.handlers.trade_create.logger.info",
                    ) as mock_logger_info,
                    mock.patch.object(
                        aiogram_types.CallbackQuery,
                        "answer",
                        new_callable=mock.AsyncMock,
                    ) as mock_callback_answer,
                ):
                    result = await trade_create.router.propagate_event(
                        "callback_query",
                        event,
                        state=mock_state,
                        user=runtime_user,
                        raw_state=None,
                    )

                self.assertIsNone(result)
                mock_utc_now.assert_called_once_with()
                mock_state.get_state.assert_awaited_once_with()
                mock_logger_info.assert_called_once_with(
                    "Rejected stale trade creation callback",
                    extra={
                        "event": "telegram.offer_fsm_stale_callback",
                        "callback_prefix": expected_prefix,
                        "trade_state": fixed_trade_state,
                        "user_id": runtime_user.id,
                    },
                )
                mock_answer.assert_awaited_once_with(
                    event,
                    received_at=sentinel_time,
                    text=STALE_TRADE_CREATION_CALLBACK_TEXT,
                    show_alert=True,
                )
                self.assertEqual(mock_answer.await_args.args, (event,))
                self.assertIs(mock_answer.await_args.args[0], event)
                self.assertEqual(
                    mock_answer.await_args.kwargs,
                    {
                        "received_at": sentinel_time,
                        "text": STALE_TRADE_CREATION_CALLBACK_TEXT,
                        "show_alert": True,
                    },
                )

                mock_state.set_state.assert_not_called()
                mock_state.clear.assert_not_called()
                mock_state.update_data.assert_not_called()
                mock_callback_answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
