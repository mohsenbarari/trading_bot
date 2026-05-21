import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from api.deps import EffectiveOwnerActor
from api.routers.trades import get_my_trades, get_trade, get_trades_with_user
from models.user import UserRole


def compile_sql(statement):
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


class FakeScalarRows:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class FakeExecuteResult:
    def __init__(self, values=None, single=None):
        self._values = values or []
        self._single = single

    def scalars(self):
        return FakeScalarRows(self._values)

    def scalar_one_or_none(self):
        return self._single


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.statements = []

    async def execute(self, _stmt):
        self.statements.append(_stmt)
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class TradesRouterReadTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_context(owner_id=5, actor_id=None, owner_role=None, actor_role=None):
        owner_user = SimpleNamespace(id=owner_id, role=owner_role)
        actor_user = SimpleNamespace(id=actor_id if actor_id is not None else owner_id, role=actor_role or owner_role)
        return EffectiveOwnerActor(
            owner_user=owner_user,
            actor_user=actor_user,
            relation=None,
            is_accountant_context=owner_user.id != actor_user.id,
        )

    async def test_get_my_trades_serializes_matching_rows(self):
        trades = [
            SimpleNamespace(id=1, offer_user_id=5, responder_user_id=11, actor_user_id=None),
            SimpleNamespace(id=2, offer_user_id=12, responder_user_id=5, actor_user_id=41),
        ]
        db = FakeDB([FakeExecuteResult(values=trades)])
        context = self.make_context(owner_id=5, actor_id=11)

        with patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ) as identity_mock, patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value={}),
        ) as relation_map_mock, patch(
            "api.routers.trades.trade_to_response",
            side_effect=[{"id": 1}, {"id": 2}],
        ) as response_mock:
            result = await get_my_trades(skip=0, limit=50, db=db, context=context)

        self.assertEqual(result, [{"id": 1}, {"id": 2}])
        identity_mock.assert_awaited_once()
        relation_map_mock.assert_awaited_once()
        self.assertTrue(relation_map_mock.await_args.kwargs["include_inactive_historical"])
        self.assertEqual(response_mock.call_count, 2)
        self.assertEqual(response_mock.call_args_list[0].args[0], trades[0])
        self.assertEqual(response_mock.call_args_list[0].kwargs["identity_map"], {})
        self.assertEqual(response_mock.call_args_list[0].kwargs["customer_relation_map"], {})
        self.assertEqual(response_mock.call_args_list[0].kwargs["viewer_context"], context)
        self.assertEqual(response_mock.call_args_list[0].kwargs["history_target_user_id"], 5)
        sql = compile_sql(db.statements[0])
        self.assertIn("trades.actor_user_id = 5", sql)

    async def test_get_trade_enforces_not_found_and_access_control(self):
        context = self.make_context(owner_id=5, actor_id=12)

        with self.assertRaises(HTTPException) as exc_info:
            await get_trade(trade_id=7, db=FakeDB([FakeExecuteResult(single=None)]), context=context)
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "معامله یافت نشد.")

        foreign_trade = SimpleNamespace(offer_user_id=9, responder_user_id=10, actor_user_id=None)
        with patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await get_trade(
                    trade_id=7,
                    db=FakeDB([FakeExecuteResult(single=foreign_trade)]),
                    context=context,
                )
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "شما به این معامله دسترسی ندارید.")

    async def test_get_trade_returns_trade_for_participant(self):
        context = self.make_context(owner_id=5, actor_id=12)
        trade = SimpleNamespace(id=7, offer_user_id=5, responder_user_id=10, actor_user_id=None)

        with patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ) as identity_mock, patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value={}),
        ) as relation_map_mock, patch("api.routers.trades.trade_to_response", return_value={"id": 7}) as response_mock:
            result = await get_trade(
                trade_id=7,
                db=FakeDB([FakeExecuteResult(single=trade)]),
                context=context,
            )

        identity_mock.assert_awaited_once()
        self.assertTrue(relation_map_mock.await_args.kwargs["include_inactive_historical"])
        response_mock.assert_called_once_with(
            trade,
            identity_map={},
            customer_relation_map={},
            viewer_context=context,
            history_target_user_id=5,
        )
        self.assertEqual(result, {"id": 7})

    async def test_get_trades_with_user_short_circuits_self_and_serializes_history(self):
        context = self.make_context(owner_id=5, actor_id=12)
        self.assertEqual(
            await get_trades_with_user(other_user_id=5, skip=0, limit=20, db=FakeDB(), context=context),
            [],
        )

        trades = [SimpleNamespace(id=11, offer_user_id=5, responder_user_id=7, actor_user_id=None)]
        with patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ) as identity_mock, patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value={}),
        ) as relation_map_mock, patch("api.routers.trades.trade_to_response", return_value={"id": 11}) as response_mock:
            result = await get_trades_with_user(
                other_user_id=7,
                skip=0,
                limit=20,
                db=FakeDB([FakeExecuteResult(values=trades)]),
                context=context,
            )

        identity_mock.assert_awaited_once()
        self.assertTrue(relation_map_mock.await_args.kwargs["include_inactive_historical"])
        response_mock.assert_called_once_with(
            trades[0],
            identity_map={},
            customer_relation_map={},
            viewer_context=context,
            history_target_user_id=7,
        )
        self.assertEqual(result, [{"id": 11}])

    async def test_get_trade_allows_effective_owner_when_actor_differs(self):
        context = self.make_context(owner_id=51, actor_id=52)
        trade = SimpleNamespace(id=17, offer_user_id=9, responder_user_id=51, actor_user_id=None)

        with patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ) as identity_mock, patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.trade_to_response", return_value={"id": 17}) as response_mock:
            result = await get_trade(
                trade_id=17,
                db=FakeDB([FakeExecuteResult(single=trade)]),
                context=context,
            )

        identity_mock.assert_awaited_once()
        response_mock.assert_called_once_with(
            trade,
            identity_map={},
            customer_relation_map={},
            viewer_context=context,
            history_target_user_id=51,
        )
        self.assertEqual(result, {"id": 17})

    async def test_get_trade_allows_customer_actor_history_row_for_self_viewer(self):
        context = self.make_context(owner_id=50)
        trade = SimpleNamespace(id=18, offer_user_id=7, responder_user_id=99, actor_user_id=50)

        with patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.trade_to_response", return_value={"id": 18}) as response_mock:
            result = await get_trade(
                trade_id=18,
                db=FakeDB([FakeExecuteResult(single=trade)]),
                context=context,
            )

        response_mock.assert_called_once_with(
            trade,
            identity_map={},
            customer_relation_map={},
            viewer_context=context,
            history_target_user_id=50,
        )
        self.assertEqual(result, {"id": 18})

    async def test_get_trades_with_user_switches_to_target_customer_history_for_owner_viewer(self):
        context = self.make_context(owner_id=7, actor_id=17)
        trades = [SimpleNamespace(id=21, offer_user_id=7, responder_user_id=99, actor_user_id=50)]
        db = FakeDB([FakeExecuteResult(values=trades)])

        async def relation_lookup(_db, user_id):
            if user_id == 50:
                return SimpleNamespace(owner_user_id=7)
            return None

        with patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=relation_lookup),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.trade_to_response", return_value={"id": 21}) as response_mock:
            result = await get_trades_with_user(other_user_id=50, skip=0, limit=20, db=db, context=context)

        self.assertEqual(result, [{"id": 21}])
        response_mock.assert_called_once_with(
            trades[0],
            identity_map={},
            customer_relation_map={},
            viewer_context=context,
            history_target_user_id=50,
        )
        sql = compile_sql(db.statements[0])
        self.assertIn("trades.offer_user_id = 50", sql)
        self.assertIn("trades.responder_user_id = 50", sql)
        self.assertIn("trades.actor_user_id = 50", sql)
        self.assertNotIn("trades.offer_user_id = 7 AND trades.responder_user_id = 50", sql)

    async def test_get_trades_with_user_switches_to_target_customer_history_for_super_admin(self):
        context = self.make_context(owner_id=900, owner_role=UserRole.SUPER_ADMIN)
        trades = [SimpleNamespace(id=22, offer_user_id=7, responder_user_id=101, actor_user_id=50)]
        db = FakeDB([FakeExecuteResult(values=trades)])

        async def relation_lookup(_db, user_id):
            if user_id == 50:
                return SimpleNamespace(owner_user_id=7)
            return None

        with patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=relation_lookup),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.trade_to_response", return_value={"id": 22}):
            result = await get_trades_with_user(other_user_id=50, skip=0, limit=20, db=db, context=context)

        self.assertEqual(result, [{"id": 22}])
        sql = compile_sql(db.statements[0])
        self.assertIn("trades.offer_user_id = 50", sql)
        self.assertIn("trades.responder_user_id = 50", sql)
        self.assertIn("trades.actor_user_id = 50", sql)

    async def test_get_trades_with_user_switches_to_target_history_for_super_admin_non_customer_target(self):
        context = self.make_context(owner_id=902, owner_role=UserRole.SUPER_ADMIN)
        trades = [SimpleNamespace(id=24, offer_user_id=51, responder_user_id=77, actor_user_id=None)]
        db = FakeDB([FakeExecuteResult(values=trades)])

        with patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.trade_to_response", return_value={"id": 24}) as response_mock:
            result = await get_trades_with_user(other_user_id=51, skip=0, limit=20, db=db, context=context)

        self.assertEqual(result, [{"id": 24}])
        response_mock.assert_called_once_with(
            trades[0],
            identity_map={},
            customer_relation_map={},
            viewer_context=context,
            history_target_user_id=51,
        )
        sql = compile_sql(db.statements[0])
        self.assertIn("trades.offer_user_id = 51", sql)
        self.assertIn("trades.responder_user_id = 51", sql)
        self.assertIn("trades.actor_user_id = 51", sql)
        self.assertNotIn("trades.offer_user_id = 902 AND trades.responder_user_id = 51", sql)

    async def test_get_trades_with_user_keeps_mutual_history_for_unauthorized_customer_view(self):
        context = self.make_context(owner_id=8, actor_id=18)
        trades = [SimpleNamespace(id=23, offer_user_id=8, responder_user_id=50, actor_user_id=None)]
        db = FakeDB([FakeExecuteResult(values=trades)])

        async def relation_lookup(_db, user_id):
            if user_id == 50:
                return SimpleNamespace(owner_user_id=7)
            return None

        with patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=relation_lookup),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.trade_to_response", return_value={"id": 23}):
            result = await get_trades_with_user(other_user_id=50, skip=0, limit=20, db=db, context=context)

        self.assertEqual(result, [{"id": 23}])
        sql = compile_sql(db.statements[0])
        self.assertIn("trades.offer_user_id = 8 AND trades.responder_user_id = 50", sql)
        self.assertIn("trades.offer_user_id = 50 AND trades.responder_user_id = 8", sql)

    async def test_get_trade_allows_owned_customer_history_row_for_owner_viewer(self):
        context = self.make_context(owner_id=7, actor_id=17)
        trade = SimpleNamespace(id=27, offer_user_id=7, responder_user_id=99, actor_user_id=50)

        async def relation_lookup(_db, user_id):
            if user_id == 50:
                return SimpleNamespace(owner_user_id=7)
            return None

        with patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=relation_lookup),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ) as identity_mock, patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.trade_to_response", return_value={"id": 27}) as response_mock:
            result = await get_trade(trade_id=27, db=FakeDB([FakeExecuteResult(single=trade)]), context=context)

        identity_mock.assert_awaited_once()
        response_mock.assert_called_once_with(
            trade,
            identity_map={},
            customer_relation_map={},
            viewer_context=context,
            history_target_user_id=7,
        )
        self.assertEqual(result, {"id": 27})

    async def test_get_trade_allows_customer_history_row_for_super_admin(self):
        context = self.make_context(owner_id=901, owner_role=UserRole.SUPER_ADMIN)
        trade = SimpleNamespace(id=28, offer_user_id=7, responder_user_id=99, actor_user_id=50)

        async def relation_lookup(_db, user_id):
            if user_id == 50:
                return SimpleNamespace(owner_user_id=7)
            return None

        with patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=relation_lookup),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.trade_to_response", return_value={"id": 28}):
            result = await get_trade(trade_id=28, db=FakeDB([FakeExecuteResult(single=trade)]), context=context)

        self.assertEqual(result, {"id": 28})

    async def test_get_trade_denies_unrelated_viewer_even_for_customer_history_row(self):
        context = self.make_context(owner_id=8, actor_id=18)
        trade = SimpleNamespace(id=29, offer_user_id=7, responder_user_id=99, actor_user_id=50)

        async def relation_lookup(_db, user_id):
            if user_id == 50:
                return SimpleNamespace(owner_user_id=7)
            return None

        with patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=relation_lookup),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await get_trade(trade_id=29, db=FakeDB([FakeExecuteResult(single=trade)]), context=context)

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "شما به این معامله دسترسی ندارید.")


if __name__ == "__main__":
    unittest.main()