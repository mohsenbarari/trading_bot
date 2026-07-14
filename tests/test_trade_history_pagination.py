import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from api.routers import trades as trades_module
from models.trade import Trade


def compile_sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


class FakeExecuteResult:
    def __init__(self, values):
        self._values = list(values)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))


class CapturingDB:
    def __init__(self, values):
        self.values = list(values)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return FakeExecuteResult(self.values)


def trade_response_for(trade_id: int) -> trades_module.TradeResponse:
    return trades_module.TradeResponse(
        id=trade_id,
        trade_number=20_000 + trade_id,
        offer_id=None,
        trade_type="buy",
        settlement_type="cash",
        commodity_id=7,
        commodity_name="stage13 commodity",
        quantity=10,
        price=100_000,
        status="completed",
        offer_user_id=11,
        offer_user_name="owner",
        responder_user_id=12,
        responder_user_name="other",
        created_at="1405/01/01 12:00",
    )


class TradeHistoryCursorTests(unittest.TestCase):
    def signature(self, **overrides):
        values = {
            "scope": "with",
            "viewer_owner_user_id": 11,
            "target_user_id": 12,
            "from_date": date(2026, 1, 1),
            "to_date": date(2026, 7, 14),
            "commodity_id": 7,
            "commodity_query": " coin ",
            "settlement_type": "cash",
            "perspective_trade_type": "buy",
        }
        values.update(overrides)
        return trades_module._trade_history_filter_signature(**values)

    def test_cursor_round_trip_is_bound_to_viewer_target_and_filters(self):
        signature = self.signature()
        position = SimpleNamespace(
            id=51,
            created_at=datetime(2026, 7, 14, 10, 30, tzinfo=timezone.utc),
        )
        cursor = trades_module._encode_trade_history_cursor(
            position,
            filter_signature=signature,
        )
        self.assertEqual(
            trades_module._decode_trade_history_cursor(
                cursor,
                expected_filter_signature=signature,
            ),
            (position.created_at, position.id),
        )
        for changed in (
            self.signature(viewer_owner_user_id=99),
            self.signature(target_user_id=98),
            self.signature(settlement_type="tomorrow"),
            self.signature(perspective_trade_type="sell"),
        ):
            with self.subTest(changed=changed), self.assertRaises(HTTPException) as raised:
                trades_module._decode_trade_history_cursor(
                    cursor,
                    expected_filter_signature=changed,
                )
            self.assertEqual(raised.exception.status_code, 400)

    def test_malformed_cursor_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            trades_module._decode_trade_history_cursor(
                "invalid!",
                expected_filter_signature=self.signature(),
            )
        self.assertEqual(raised.exception.status_code, 400)

    def test_query_filters_trade_type_from_viewer_perspective_and_uses_stable_cursor(self):
        query = trades_module._build_my_trades_query(
            11,
            from_date=date(2026, 1, 1),
            to_date=date(2026, 7, 14),
            commodity_id=7,
            commodity_query=None,
            settlement_type="cash",
            perspective_trade_type="buy",
        )
        query = trades_module._build_trade_history_page_query(
            query,
            cursor_position=(datetime(2026, 7, 14, 10, 30, tzinfo=timezone.utc), 51),
            limit=50,
        )
        sql = compile_sql(query)

        self.assertIn("trades.commodity_id = 7", sql)
        self.assertIn("trades.settlement_type = 'CASH'", sql)
        self.assertIn("trades.responder_user_id = 11 AND trades.trade_type = 'BUY'", sql)
        self.assertIn("trades.offer_user_id = 11 AND trades.trade_type = 'SELL'", sql)
        self.assertIn("trades.created_at <", sql)
        self.assertIn("trades.id < 51", sql)
        self.assertIn("ORDER BY trades.created_at DESC, trades.id DESC", sql)
        self.assertIn("LIMIT 51", sql)

    def test_model_declares_participant_cursor_indexes(self):
        indexes = {index.name: index for index in Trade.__table__.indexes}
        for name, participant_column in (
            ("ix_trades_offer_user_history_cursor", "offer_user_id"),
            ("ix_trades_responder_history_cursor", "responder_user_id"),
        ):
            index = indexes[name]
            self.assertEqual(
                [
                    expression.element.name if hasattr(expression, "element") else expression.name
                    for expression in index.expressions
                ],
                [participant_column, "created_at", "id"],
            )


class TradeHistoryPageEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_my_page_uses_extra_row_for_metadata_only(self):
        created_at = datetime(2026, 7, 14, 10, 30, tzinfo=timezone.utc)
        db = CapturingDB([
            SimpleNamespace(id=52, created_at=created_at),
            SimpleNamespace(id=51, created_at=created_at),
        ])
        user = SimpleNamespace(id=11)
        context = SimpleNamespace(owner_user=user, actor_user=user, is_accountant_context=False)

        with patch.object(
            trades_module,
            "_serialize_trade_history_rows",
            new=AsyncMock(return_value=[trade_response_for(52)]),
        ):
            response = await trades_module.get_my_trades_page(
                cursor=None,
                limit=1,
                from_date=None,
                to_date=None,
                commodity_id=None,
                commodity_query=None,
                settlement_type=None,
                trade_type=None,
                db=db,
                context=context,
            )

        self.assertEqual([item.id for item in response.items], [52])
        self.assertTrue(response.has_more)
        self.assertEqual(response.page_size, 1)
        self.assertIsNotNone(response.next_cursor)
        signature = trades_module._trade_history_filter_signature(
            scope="my",
            viewer_owner_user_id=11,
            target_user_id=None,
            from_date=None,
            to_date=None,
            commodity_id=None,
            commodity_query=None,
            settlement_type=None,
            perspective_trade_type=None,
        )
        self.assertEqual(
            trades_module._decode_trade_history_cursor(
                response.next_cursor,
                expected_filter_signature=signature,
            ),
            (created_at, 52),
        )

    async def test_with_page_rechecks_authorized_query_on_every_request(self):
        created_at = datetime(2026, 7, 14, 10, 30, tzinfo=timezone.utc)
        db = CapturingDB([SimpleNamespace(id=70, created_at=created_at)])
        user = SimpleNamespace(id=11, role="standard")
        context = SimpleNamespace(owner_user=user, actor_user=user, is_accountant_context=False)
        relation = SimpleNamespace(owner_user_id=11, customer_user_id=12)
        query_builder = AsyncMock(return_value=(select(Trade), relation))

        with patch.object(
            trades_module,
            "_build_trades_with_user_query",
            new=query_builder,
        ), patch.object(
            trades_module,
            "_serialize_trade_history_rows",
            new=AsyncMock(return_value=[trade_response_for(70)]),
        ):
            response = await trades_module.get_trades_with_user_page(
                other_user_id=12,
                cursor=None,
                limit=50,
                from_date=None,
                to_date=None,
                commodity_id=None,
                commodity_query=None,
                settlement_type=None,
                trade_type=None,
                db=db,
                context=context,
            )

        self.assertEqual([item.id for item in response.items], [70])
        query_builder.assert_awaited_once()
        self.assertEqual(query_builder.await_args.kwargs["other_user_id"], 12)
        self.assertEqual(query_builder.await_args.kwargs["context"], context)


if __name__ == "__main__":
    unittest.main()
