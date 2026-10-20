"""Hermetic characterization of get_trades_with_user_page self-target early return.

Evidence limits: tests direct synthetic handler response, early-return ordering,
and suppressed downstream/database boundary calls only; does not claim HTTP/Depends/auth,
cursor/query/SQL/DB execution, serialization, Redis/network delivery, retry, or origin coverage.
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


class WithUserTradePageSelfTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_self_target_returns_empty_before_signature_cursor_query_and_database(self) -> None:
        original_handler = trades_router.get_trades_with_user_page
        original_response_model = trades_router.TradeHistoryPageResponse
        sync_boundary_names = (
            "_trade_history_filter_signature",
            "_decode_trade_history_cursor",
            "_build_trade_history_page_query",
            "_encode_trade_history_cursor",
            "_is_super_admin_trade_history_viewer",
        )
        async_boundary_names = (
            "_build_trades_with_user_query",
            "_serialize_trade_history_rows",
        )
        originals = {
            attr: getattr(trades_router, attr)
            for attr in (*sync_boundary_names, *async_boundary_names)
        }

        owner_user = SimpleNamespace(id=511, role=UserRole.STANDARD)
        actor_user = SimpleNamespace(id=612, role=UserRole.STANDARD)
        relation = SimpleNamespace(id=713, owner_user_id=511, accountant_user_id=612)
        owner_snapshot = dict(owner_user.__dict__)
        actor_snapshot = dict(actor_user.__dict__)
        relation_snapshot = dict(relation.__dict__)
        context = EffectiveOwnerActor(
            owner_user=owner_user,
            actor_user=actor_user,
            relation=relation,
            is_accountant_context=True,
        )

        other_user_id = owner_user.id
        self.assertEqual(other_user_id, owner_user.id)
        self.assertNotEqual(owner_user.id, actor_user.id)
        self.assertNotEqual(other_user_id, actor_user.id)
        self.assertIsNot(owner_user, actor_user)
        self.assertIsNot(owner_user, relation)
        self.assertIsNot(actor_user, relation)

        cursor = "!!!invalid-nonempty-self-cursor-766f!!!"
        limit = 25
        from_date = date(2026, 3, 1)
        to_date = date(2026, 3, 20)
        commodity_id = 23
        commodity_query = "  melted gold 766f  "
        settlement_type = SettlementType.CASH.value
        trade_type = TradeType.BUY.value

        async_db_names = (
            "execute",
            "get",
            "scalar",
            "scalars",
            "commit",
            "delete",
            "flush",
            "refresh",
            "rollback",
        )
        sync_db_names = ("add", "add_all")
        db = SimpleNamespace(
            **{
                name: AsyncMock(
                    name=f"db.{name}",
                    side_effect=AssertionError(f"unexpected db.{name} traversal"),
                )
                for name in async_db_names
            },
            **{
                name: Mock(
                    name=f"db.{name}",
                    side_effect=AssertionError(f"unexpected db.{name} traversal"),
                )
                for name in sync_db_names
            },
        )

        sync_mocks = {
            attr: Mock(
                name=attr,
                side_effect=AssertionError(f"unexpected {attr} traversal"),
            )
            for attr in sync_boundary_names
        }
        async_mocks = {
            attr: AsyncMock(
                name=attr,
                side_effect=AssertionError(f"unexpected {attr} traversal"),
            )
            for attr in async_boundary_names
        }

        with ExitStack() as stack:
            for attr, mock_obj in {**sync_mocks, **async_mocks}.items():
                stack.enter_context(patch.object(trades_router, attr, mock_obj))

            self.assertIs(trades_router.get_trades_with_user_page, original_handler)
            self.assertIs(trades_router.TradeHistoryPageResponse, original_response_model)
            for attr, mock_obj in {**sync_mocks, **async_mocks}.items():
                self.assertIs(getattr(trades_router, attr), mock_obj)

            response = await trades_router.get_trades_with_user_page(
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

            self.assertIs(type(response), original_response_model)
            self.assertEqual(
                response.model_dump(),
                {
                    "items": [],
                    "next_cursor": None,
                    "has_more": False,
                    "page_size": 0,
                },
            )
            self.assertEqual(response.items, [])
            self.assertIsNone(response.next_cursor)
            self.assertFalse(response.has_more)
            self.assertEqual(response.page_size, 0)
            self.assertNotEqual(limit, 0)

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
        self.assertIs(trades_router.TradeHistoryPageResponse, original_response_model)
        for attr, orig_obj in originals.items():
            self.assertIs(getattr(trades_router, attr), orig_obj)

        self.assertIs(context.owner_user, owner_user)
        self.assertIs(context.actor_user, actor_user)
        self.assertIs(context.relation, relation)
        self.assertTrue(context.is_accountant_context)
        self.assertEqual(owner_user.__dict__, owner_snapshot)
        self.assertEqual(actor_user.__dict__, actor_snapshot)
        self.assertEqual(relation.__dict__, relation_snapshot)
