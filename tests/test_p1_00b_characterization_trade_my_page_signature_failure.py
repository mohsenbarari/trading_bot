from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import ExitStack
from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import api.routers.trades as trades_router


class MyTradePageSignatureFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_signature_exception_precedes_cursor_query_and_database(self) -> None:
        original_handler = trades_router.get_my_trades_page
        original_router_attrs = {
            "_trade_history_filter_signature": trades_router._trade_history_filter_signature,
            "_decode_trade_history_cursor": trades_router._decode_trade_history_cursor,
            "_build_my_trades_query": trades_router._build_my_trades_query,
            "_build_trade_history_page_query": trades_router._build_trade_history_page_query,
            "_serialize_trade_history_rows": trades_router._serialize_trade_history_rows,
            "_encode_trade_history_cursor": trades_router._encode_trade_history_cursor,
            "TradeHistoryPageResponse": trades_router.TradeHistoryPageResponse,
        }

        owner = SimpleNamespace(id=766401, role="trader", label="owner-766d")
        actor = SimpleNamespace(id=766402, role="accountant", label="actor-766d")
        context = SimpleNamespace(
            owner_user=owner,
            actor_user=actor,
            relation=None,
            is_accountant_context=True,
        )

        cursor = "766d.synthetic.cursor"
        limit = 7
        from_date = date(2026, 10, 8)
        to_date = date(2026, 10, 19)
        commodity_id = 17
        commodity_query = "  synthetic coin  "
        settlement_type = "tomorrow"
        trade_type = "sell"

        failure = RuntimeError("766d my trade page filter signature failure")
        signature_mock = Mock(side_effect=failure)

        sync_router_mocks = {
            name: Mock(
                side_effect=AssertionError(
                    f"unexpected call to router attribute {name}"
                )
            )
            for name in (
                "_decode_trade_history_cursor",
                "_build_my_trades_query",
                "_build_trade_history_page_query",
                "_encode_trade_history_cursor",
                "TradeHistoryPageResponse",
            )
        }
        serialize_mock = AsyncMock(
            side_effect=AssertionError(
                "unexpected call to router attribute _serialize_trade_history_rows"
            )
        )

        async_db_mocks = {
            name: AsyncMock(
                side_effect=AssertionError(f"unexpected async db.{name} call")
            )
            for name in ("execute", "commit", "delete", "flush", "refresh", "rollback")
        }
        sync_db_mocks = {
            name: Mock(
                side_effect=AssertionError(f"unexpected sync db.{name} call")
            )
            for name in ("add", "add_all")
        }
        db = SimpleNamespace(**async_db_mocks, **sync_db_mocks)

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    trades_router,
                    "_trade_history_filter_signature",
                    new=signature_mock,
                )
            )
            for attr_name, attr_mock in sync_router_mocks.items():
                stack.enter_context(
                    patch.object(trades_router, attr_name, new=attr_mock)
                )
            stack.enter_context(
                patch.object(
                    trades_router,
                    "_serialize_trade_history_rows",
                    new=serialize_mock,
                )
            )

            self.assertIs(trades_router.get_my_trades_page, original_handler)

            with self.assertRaises(RuntimeError) as raised:
                await original_handler(
                    cursor=cursor,
                    limit=limit,
                    from_date=from_date,
                    to_date=to_date,
                    commodity_id=commodity_id,
                    commodity_query=commodity_query,
                    settlement_type=settlement_type,
                    trade_type=trade_type,
                    db=db,
                    context=context,
                )

            self.assertIs(trades_router.get_my_trades_page, original_handler)

        self.assertIs(raised.exception, failure)
        signature_mock.assert_called_once_with(
            scope="my",
            viewer_owner_user_id=766401,
            target_user_id=None,
            from_date=date(2026, 10, 8),
            to_date=date(2026, 10, 19),
            commodity_id=17,
            commodity_query="  synthetic coin  ",
            settlement_type="tomorrow",
            perspective_trade_type="sell",
        )
        self.assertEqual(signature_mock.call_count, 1)
        call_args, call_kwargs = signature_mock.call_args
        self.assertEqual(call_args, ())
        self.assertEqual(
            set(call_kwargs.keys()),
            {
                "scope",
                "viewer_owner_user_id",
                "target_user_id",
                "from_date",
                "to_date",
                "commodity_id",
                "commodity_query",
                "settlement_type",
                "perspective_trade_type",
            },
        )

        for attr_name, attr_mock in sync_router_mocks.items():
            self.assertEqual(attr_mock.call_count, 0, attr_name)
        self.assertEqual(serialize_mock.call_count, 0)
        self.assertEqual(serialize_mock.await_count, 0)

        for db_name, db_mock in async_db_mocks.items():
            self.assertEqual(db_mock.call_count, 0, db_name)
            self.assertEqual(db_mock.await_count, 0, db_name)
        for db_name, db_mock in sync_db_mocks.items():
            self.assertEqual(db_mock.call_count, 0, db_name)

        self.assertIs(context.owner_user, owner)
        self.assertIs(context.actor_user, actor)
        self.assertIsNone(context.relation)
        self.assertTrue(context.is_accountant_context)
        self.assertEqual(owner.__dict__, {"id": 766401, "role": "trader", "label": "owner-766d"})
        self.assertEqual(actor.__dict__, {"id": 766402, "role": "accountant", "label": "actor-766d"})
        self.assertEqual(cursor, "766d.synthetic.cursor")
        self.assertEqual(limit, 7)
        self.assertEqual(from_date, date(2026, 10, 8))
        self.assertEqual(to_date, date(2026, 10, 19))
        self.assertEqual(commodity_id, 17)
        self.assertEqual(commodity_query, "  synthetic coin  ")
        self.assertEqual(settlement_type, "tomorrow")
        self.assertEqual(trade_type, "sell")

        self.assertIs(trades_router.get_my_trades_page, original_handler)
        for attr_name, original_obj in original_router_attrs.items():
            self.assertIs(getattr(trades_router, attr_name), original_obj, attr_name)


if __name__ == "__main__":
    unittest.main()
