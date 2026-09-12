"""Direct-characterization tests for api.routers.customers.get_my_customer_trade_stats failure boundaries under Stage P1-00b.

Assignment: WA-P1-00B-FREEZE-20260904-001
Gap: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-GET-CUSTOMERS-GET-MY-CUSTOMER-TRADE-STATS-L708
Target: api.routers.customers.get_my_customer_trade_stats

This partial test-only unit neither promotes inventory evidence nor closes the blocker or Stage.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.customers import get_my_customer_trade_stats  # noqa: E402


class CharacterizeGetMyCustomerTradeStatsFailureBoundariesTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic unit tests characterizing get_my_customer_trade_stats failure boundaries."""

    @patch(
        "api.routers.customers.calculate_customer_trade_commission_profit",
        new_callable=MagicMock,
    )
    @patch("api.routers.customers.select", new_callable=MagicMock)
    @patch(
        "api.routers.customers._customer_relation_trade_stats_bounds",
        new_callable=MagicMock,
    )
    @patch("api.routers.customers.utc_now_naive", new_callable=MagicMock)
    @patch(
        "api.routers.customers.get_owner_customer_relation",
        new_callable=AsyncMock,
    )
    @patch("api.routers.customers.ensure_owner_context", new_callable=AsyncMock)
    async def test_get_my_customer_trade_stats_invalid_days(
        self,
        mock_ensure_owner_context: AsyncMock,
        mock_get_owner_customer_relation: AsyncMock,
        mock_utc_now_naive: MagicMock,
        mock_customer_relation_trade_stats_bounds: MagicMock,
        mock_select: MagicMock,
        mock_calculate_customer_trade_commission_profit: MagicMock,
    ) -> None:
        call_order: list[str] = []

        async def fake_ensure_owner_context(
            _context: object, _db: object
        ) -> None:
            call_order.append("ensure_owner_context")

        mock_ensure_owner_context.side_effect = fake_ensure_owner_context

        owner_user_id = 42
        relation_id = 101
        context = SimpleNamespace(
            owner_user=SimpleNamespace(id=owner_user_id),
            actor_user=SimpleNamespace(id=owner_user_id),
            is_accountant_context=False,
            relation=None,
        )
        fake_db = MagicMock()
        fake_db.execute = AsyncMock()

        invalid_days = 5

        with self.assertRaises(HTTPException) as ctx:
            await get_my_customer_trade_stats(
                relation_id=relation_id,
                days=invalid_days,
                context=context,
                db=fake_db,
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "بازه آمار مشتری نامعتبر است")
        self.assertEqual(call_order, ["ensure_owner_context"])
        mock_ensure_owner_context.assert_awaited_once_with(context, fake_db)
        mock_get_owner_customer_relation.assert_not_called()
        mock_get_owner_customer_relation.assert_not_awaited()
        fake_db.execute.assert_not_called()
        fake_db.execute.assert_not_awaited()
        mock_utc_now_naive.assert_not_called()
        mock_customer_relation_trade_stats_bounds.assert_not_called()
        mock_select.assert_not_called()
        mock_calculate_customer_trade_commission_profit.assert_not_called()

    @patch(
        "api.routers.customers.calculate_customer_trade_commission_profit",
        new_callable=MagicMock,
    )
    @patch("api.routers.customers.select", new_callable=MagicMock)
    @patch(
        "api.routers.customers._customer_relation_trade_stats_bounds",
        new_callable=MagicMock,
    )
    @patch("api.routers.customers.utc_now_naive", new_callable=MagicMock)
    @patch(
        "api.routers.customers.get_owner_customer_relation",
        new_callable=AsyncMock,
    )
    @patch("api.routers.customers.ensure_owner_context", new_callable=AsyncMock)
    async def test_get_my_customer_trade_stats_relation_lookup_failure(
        self,
        mock_ensure_owner_context: AsyncMock,
        mock_get_owner_customer_relation: AsyncMock,
        mock_utc_now_naive: MagicMock,
        mock_customer_relation_trade_stats_bounds: MagicMock,
        mock_select: MagicMock,
        mock_calculate_customer_trade_commission_profit: MagicMock,
    ) -> None:
        sentinel_error = RuntimeError(
            "get_owner_customer_relation sentinel failure"
        )
        call_order: list[str] = []

        async def fake_ensure_owner_context(
            _context: object, _db: object
        ) -> None:
            call_order.append("ensure_owner_context")

        async def fake_get_owner_customer_relation(
            _db: object, *, owner_user_id: int, relation_id: int
        ) -> object:
            call_order.append("get_owner_customer_relation")
            raise sentinel_error

        mock_ensure_owner_context.side_effect = fake_ensure_owner_context
        mock_get_owner_customer_relation.side_effect = (
            fake_get_owner_customer_relation
        )

        owner_user_id = 42
        relation_id = 101
        context = SimpleNamespace(
            owner_user=SimpleNamespace(id=owner_user_id),
            actor_user=SimpleNamespace(id=owner_user_id),
            is_accountant_context=False,
            relation=None,
        )
        fake_db = MagicMock()
        fake_db.execute = AsyncMock()

        valid_days = 7

        with self.assertRaises(RuntimeError) as ctx:
            await get_my_customer_trade_stats(
                relation_id=relation_id,
                days=valid_days,
                context=context,
                db=fake_db,
            )

        self.assertIs(ctx.exception, sentinel_error)
        self.assertEqual(
            call_order,
            ["ensure_owner_context", "get_owner_customer_relation"],
        )
        mock_ensure_owner_context.assert_awaited_once_with(context, fake_db)
        mock_get_owner_customer_relation.assert_awaited_once_with(
            fake_db, owner_user_id=owner_user_id, relation_id=relation_id
        )
        fake_db.execute.assert_not_called()
        fake_db.execute.assert_not_awaited()
        mock_utc_now_naive.assert_not_called()
        mock_customer_relation_trade_stats_bounds.assert_not_called()
        mock_select.assert_not_called()
        mock_calculate_customer_trade_commission_profit.assert_not_called()

    @patch(
        "api.routers.customers.calculate_customer_trade_commission_profit",
        new_callable=MagicMock,
    )
    @patch("api.routers.customers.select", new_callable=MagicMock)
    @patch(
        "api.routers.customers._customer_relation_trade_stats_bounds",
        new_callable=MagicMock,
    )
    @patch("api.routers.customers.utc_now_naive", new_callable=MagicMock)
    @patch(
        "api.routers.customers.get_owner_customer_relation",
        new_callable=AsyncMock,
    )
    @patch("api.routers.customers.ensure_owner_context", new_callable=AsyncMock)
    async def test_get_my_customer_trade_stats_unregistered_relation_failure(
        self,
        mock_ensure_owner_context: AsyncMock,
        mock_get_owner_customer_relation: AsyncMock,
        mock_utc_now_naive: MagicMock,
        mock_customer_relation_trade_stats_bounds: MagicMock,
        mock_select: MagicMock,
        mock_calculate_customer_trade_commission_profit: MagicMock,
    ) -> None:
        call_order: list[str] = []

        async def fake_ensure_owner_context(
            _context: object, _db: object
        ) -> None:
            call_order.append("ensure_owner_context")

        owner_user_id = 42
        relation_id = 101
        fake_relation = SimpleNamespace(
            id=relation_id,
            owner_user_id=owner_user_id,
            customer_user_id=None,
        )

        async def fake_get_owner_customer_relation(
            _db: object, *, owner_user_id: int, relation_id: int
        ) -> object:
            call_order.append("get_owner_customer_relation")
            return fake_relation

        mock_ensure_owner_context.side_effect = fake_ensure_owner_context
        mock_get_owner_customer_relation.side_effect = (
            fake_get_owner_customer_relation
        )

        context = SimpleNamespace(
            owner_user=SimpleNamespace(id=owner_user_id),
            actor_user=SimpleNamespace(id=owner_user_id),
            is_accountant_context=False,
            relation=None,
        )
        fake_db = MagicMock()
        fake_db.execute = AsyncMock()

        valid_days = 7

        with self.assertRaises(HTTPException) as ctx:
            await get_my_customer_trade_stats(
                relation_id=relation_id,
                days=valid_days,
                context=context,
                db=fake_db,
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(
            ctx.exception.detail, "آمار فقط برای مشتری ثبت\u200cشده قابل محاسبه است"
        )
        self.assertEqual(
            call_order,
            ["ensure_owner_context", "get_owner_customer_relation"],
        )
        mock_ensure_owner_context.assert_awaited_once_with(context, fake_db)
        mock_get_owner_customer_relation.assert_awaited_once_with(
            fake_db, owner_user_id=owner_user_id, relation_id=relation_id
        )
        fake_db.execute.assert_not_called()
        fake_db.execute.assert_not_awaited()
        mock_utc_now_naive.assert_not_called()
        mock_customer_relation_trade_stats_bounds.assert_not_called()
        mock_select.assert_not_called()
        mock_calculate_customer_trade_commission_profit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
