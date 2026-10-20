"""Hermetic characterization of get_trades_with_user_page filter-signature failure boundary.

Evidence limits: tests direct handler invocation order and exception propagation only;
does not claim HTTP/Depends/auth, cursor/query/serialization execution, SQL/DB persistence,
network/Redis delivery, concurrency, retry, or origin coverage.
"""
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
from core.enums import SettlementType
from core.services.accountant_relation_service import EffectiveOwnerActor
from models.trade import TradeType
from models.user import UserRole


class WithUserTradePageSignatureFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_signature_exception_precedes_cursor_query_and_database(self) -> None:
        original_handler = trades_router.get_trades_with_user_page
        originals = {
            "_trade_history_filter_signature": trades_router._trade_history_filter_signature,
            "_decode_trade_history_cursor": trades_router._decode_trade_history_cursor,
            "_build_trades_with_user_query": trades_router._build_trades_with_user_query,
            "_build_trade_history_page_query": trades_router._build_trade_history_page_query,
            "_serialize_trade_history_rows": trades_router._serialize_trade_history_rows,
            "_encode_trade_history_cursor": trades_router._encode_trade_history_cursor,
            "_is_super_admin_trade_history_viewer": trades_router._is_super_admin_trade_history_viewer,
            "TradeHistoryPageResponse": trades_router.TradeHistoryPageResponse,
        }

        owner_user = SimpleNamespace(id=501, role=UserRole.STANDARD, full_name="Owner 501")
        actor_user = SimpleNamespace(id=602, role=UserRole.STANDARD, full_name="Actor 602")
        relation = SimpleNamespace(id=703, owner_user_id=501, accountant_user_id=602)
        owner_snapshot = dict(owner_user.__dict__)
        actor_snapshot = dict(actor_user.__dict__)
        relation_snapshot = dict(relation.__dict__)
        context = EffectiveOwnerActor(
            owner_user=owner_user,
            actor_user=actor_user,
            relation=relation,
            is_accountant_context=True,
        )

        other_user_id = 804
        self.assertNotEqual(other_user_id, owner_user.id)
        self.assertNotEqual(other_user_id, actor_user.id)
        self.assertNotEqual(owner_user.id, actor_user.id)

        cursor = "synthetic-with-cursor-766e"
        limit = 25
        from_date = date(2026, 2, 1)
        to_date = date(2026, 2, 20)
        commodity_id = 19
        commodity_query = "  gold bar 766e  "
        settlement_type = SettlementType.TOMORROW.value
        trade_type = TradeType.SELL.value

        async_db_names = ("execute", "commit", "delete", "flush", "refresh", "rollback")
        sync_db_names = ("add", "add_all")
        db = SimpleNamespace(
            **{name: AsyncMock(name=f"db.{name}") for name in async_db_names},
            **{name: Mock(name=f"db.{name}") for name in sync_db_names},
        )

        signature_error = RuntimeError("with-user filter signature failure 766e")
        signature_mock = Mock(name="_trade_history_filter_signature", side_effect=signature_error)
        sync_mocks = {
            "_decode_trade_history_cursor": Mock(name="_decode_trade_history_cursor"),
            "_build_trade_history_page_query": Mock(name="_build_trade_history_page_query"),
            "_encode_trade_history_cursor": Mock(name="_encode_trade_history_cursor"),
            "_is_super_admin_trade_history_viewer": Mock(name="_is_super_admin_trade_history_viewer"),
            "TradeHistoryPageResponse": Mock(name="TradeHistoryPageResponse"),
        }
        async_mocks = {
            "_build_trades_with_user_query": AsyncMock(name="_build_trades_with_user_query"),
            "_serialize_trade_history_rows": AsyncMock(name="_serialize_trade_history_rows"),
        }

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(trades_router, "_trade_history_filter_signature", signature_mock)
            )
            for attr, mock_obj in {**sync_mocks, **async_mocks}.items():
                stack.enter_context(patch.object(trades_router, attr, mock_obj))

            self.assertIs(trades_router.get_trades_with_user_page, original_handler)
            self.assertIs(trades_router._trade_history_filter_signature, signature_mock)
            for attr, mock_obj in {**sync_mocks, **async_mocks}.items():
                self.assertIs(getattr(trades_router, attr), mock_obj)

            with self.assertRaises(RuntimeError) as raised:
                await trades_router.get_trades_with_user_page(
                    other_user_id=other_user_id,
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

            self.assertIs(raised.exception, signature_error)
            self.assertEqual(signature_mock.call_count, 1)
            self.assertEqual(signature_mock.call_args.args, ())
            self.assertEqual(
                signature_mock.call_args.kwargs,
                {
                    "scope": "with",
                    "viewer_owner_user_id": owner_user.id,
                    "target_user_id": other_user_id,
                    "from_date": from_date,
                    "to_date": to_date,
                    "commodity_id": commodity_id,
                    "commodity_query": commodity_query,
                    "settlement_type": settlement_type,
                    "perspective_trade_type": trade_type,
                },
            )
            self.assertIs(signature_mock.call_args.kwargs["from_date"], from_date)
            self.assertIs(signature_mock.call_args.kwargs["to_date"], to_date)

            for mock_obj in sync_mocks.values():
                self.assertEqual(mock_obj.call_count, 0)
            for async_mock_obj in async_mocks.values():
                self.assertEqual(async_mock_obj.call_count, 0)
                self.assertEqual(async_mock_obj.await_count, 0)

            for name in async_db_names:
                boundary = getattr(db, name)
                self.assertEqual(boundary.call_count, 0)
                self.assertEqual(boundary.await_count, 0)
            for name in sync_db_names:
                self.assertEqual(getattr(db, name).call_count, 0)

        self.assertIs(trades_router.get_trades_with_user_page, original_handler)
        for attr, orig_obj in originals.items():
            self.assertIs(getattr(trades_router, attr), orig_obj)

        self.assertIs(context.owner_user, owner_user)
        self.assertIs(context.actor_user, actor_user)
        self.assertIs(context.relation, relation)
        self.assertTrue(context.is_accountant_context)
        self.assertEqual(owner_user.__dict__, owner_snapshot)
        self.assertEqual(actor_user.__dict__, actor_snapshot)
        self.assertEqual(relation.__dict__, relation_snapshot)
