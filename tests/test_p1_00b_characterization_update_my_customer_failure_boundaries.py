"""Direct-characterization tests for api.routers.customers.update_my_customer failure boundaries under P1-00b.

Characterizes failure boundaries under WA-P1-00B-FREEZE-20260904-001.
Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Gap: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-PATCH-CUSTOMERS-UPDATE-MY-CUSTOMER-L674
Target: api.routers.customers.update_my_customer

This unit is test-only current-behavior freeze evidence and does not change runtime/product
behavior, promote inventory evidence, reduce the blocker, or close Stage P1-00b.
This partial test-only unit does not promote inventory evidence, reduce the blocker, or close the Stage.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import schemas  # noqa: E402
from api.routers.customers import update_my_customer  # noqa: E402
from models.customer_relation import CustomerTier  # noqa: E402


class CharacterizeUpdateMyCustomerFailureBoundariesTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic unit tests characterizing update_my_customer dependency failure boundaries."""

    @patch("api.routers.customers.serialize_customer_relation", new_callable=MagicMock)
    @patch("api.routers.customers.audit_log", new_callable=MagicMock)
    @patch("api.routers.customers.load_customer_relation_invitation_map", new_callable=AsyncMock)
    @patch("api.routers.customers.get_owner_customer_relation", new_callable=AsyncMock)
    @patch("api.routers.customers.update_owner_customer_relation", new_callable=AsyncMock)
    @patch("api.routers.customers.ensure_owner_context", new_callable=AsyncMock)
    async def test_update_my_customer_update_service_failure(
        self,
        mock_ensure_owner_context: AsyncMock,
        mock_update_owner_customer_relation: AsyncMock,
        mock_get_owner_customer_relation: AsyncMock,
        mock_load_customer_relation_invitation_map: AsyncMock,
        mock_audit_log: MagicMock,
        mock_serialize_customer_relation: MagicMock,
    ) -> None:
        sentinel_error = RuntimeError(
            "update_owner_customer_relation sentinel failure"
        )
        call_order: list[str] = []

        async def fake_ensure_owner_context(
            _context: object, _db: object
        ) -> None:
            call_order.append("ensure_owner_context")

        async def fake_update_owner_customer_relation(
            _db: object,
            *,
            owner_user_id: int,
            relation_id: int,
            update_data: dict[str, object],
        ) -> object:
            call_order.append("update_owner_customer_relation")
            raise sentinel_error

        mock_ensure_owner_context.side_effect = fake_ensure_owner_context
        mock_update_owner_customer_relation.side_effect = (
            fake_update_owner_customer_relation
        )

        owner_user_id = 301
        route_relation_id = 701
        context = SimpleNamespace(owner_user=SimpleNamespace(id=owner_user_id))
        fake_db = object()

        payload = schemas.CustomerRelationUpdate(
            customer_tier=CustomerTier.TIER_2,
            commission_rate=2.75,
            min_trade_quantity=15,
            max_trade_quantity=150,
            max_daily_trades=20,
            max_daily_commodity_volume=30000,
        )
        expected_update_data = payload.model_dump(exclude_unset=True)
        self.assertEqual(
            expected_update_data,
            {
                "customer_tier": CustomerTier.TIER_2,
                "commission_rate": 2.75,
                "min_trade_quantity": 15,
                "max_trade_quantity": 150,
                "max_daily_trades": 20,
                "max_daily_commodity_volume": 30000,
            },
        )

        with self.assertRaises(RuntimeError) as ctx:
            await update_my_customer(
                relation_id=route_relation_id,
                payload=payload,
                context=context,
                db=fake_db,
            )

        self.assertIs(ctx.exception, sentinel_error)
        self.assertEqual(
            call_order,
            ["ensure_owner_context", "update_owner_customer_relation"],
        )
        mock_ensure_owner_context.assert_awaited_once_with(context, fake_db)
        mock_update_owner_customer_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_user_id,
            relation_id=route_relation_id,
            update_data=expected_update_data,
        )
        mock_get_owner_customer_relation.assert_not_called()
        mock_get_owner_customer_relation.assert_not_awaited()
        mock_load_customer_relation_invitation_map.assert_not_called()
        mock_load_customer_relation_invitation_map.assert_not_awaited()
        mock_audit_log.assert_not_called()
        mock_serialize_customer_relation.assert_not_called()

    @patch("api.routers.customers.serialize_customer_relation", new_callable=MagicMock)
    @patch("api.routers.customers.audit_log", new_callable=MagicMock)
    @patch("api.routers.customers.load_customer_relation_invitation_map", new_callable=AsyncMock)
    @patch("api.routers.customers.get_owner_customer_relation", new_callable=AsyncMock)
    @patch("api.routers.customers.update_owner_customer_relation", new_callable=AsyncMock)
    @patch("api.routers.customers.ensure_owner_context", new_callable=AsyncMock)
    async def test_update_my_customer_reload_after_update_failure(
        self,
        mock_ensure_owner_context: AsyncMock,
        mock_update_owner_customer_relation: AsyncMock,
        mock_get_owner_customer_relation: AsyncMock,
        mock_load_customer_relation_invitation_map: AsyncMock,
        mock_audit_log: MagicMock,
        mock_serialize_customer_relation: MagicMock,
    ) -> None:
        sentinel_error = RuntimeError(
            "get_owner_customer_relation reload sentinel failure"
        )
        call_order: list[str] = []

        async def fake_ensure_owner_context(
            _context: object, _db: object
        ) -> None:
            call_order.append("ensure_owner_context")

        owner_user_id = 302
        route_relation_id = 811
        updated_relation_id = 922
        self.assertNotEqual(route_relation_id, updated_relation_id)

        stale_relation = SimpleNamespace(
            id=updated_relation_id,
            owner_user_id=owner_user_id,
            customer_user_id=456,
            customer_tier=CustomerTier.TIER_2,
            status="pending",
            invitation_token="token-stale-relation-test2",
        )

        async def fake_update_owner_customer_relation(
            _db: object,
            *,
            owner_user_id: int,
            relation_id: int,
            update_data: dict[str, object],
        ) -> object:
            call_order.append("update_owner_customer_relation")
            return stale_relation

        async def fake_get_owner_customer_relation(
            _db: object,
            *,
            owner_user_id: int,
            relation_id: int,
        ) -> object:
            call_order.append("get_owner_customer_relation")
            raise sentinel_error

        mock_ensure_owner_context.side_effect = fake_ensure_owner_context
        mock_update_owner_customer_relation.side_effect = (
            fake_update_owner_customer_relation
        )
        mock_get_owner_customer_relation.side_effect = (
            fake_get_owner_customer_relation
        )

        context = SimpleNamespace(owner_user=SimpleNamespace(id=owner_user_id))
        fake_db = object()

        payload = schemas.CustomerRelationUpdate(
            customer_tier=CustomerTier.TIER_1,
            commission_rate=0.5,
            min_trade_quantity=5,
            max_trade_quantity=80,
            max_daily_trades=10,
            max_daily_commodity_volume=12000,
        )
        expected_update_data = payload.model_dump(exclude_unset=True)
        self.assertEqual(
            expected_update_data,
            {
                "customer_tier": CustomerTier.TIER_1,
                "commission_rate": 0.5,
                "min_trade_quantity": 5,
                "max_trade_quantity": 80,
                "max_daily_trades": 10,
                "max_daily_commodity_volume": 12000,
            },
        )

        with self.assertRaises(RuntimeError) as ctx:
            await update_my_customer(
                relation_id=route_relation_id,
                payload=payload,
                context=context,
                db=fake_db,
            )

        self.assertIs(ctx.exception, sentinel_error)
        self.assertEqual(
            call_order,
            [
                "ensure_owner_context",
                "update_owner_customer_relation",
                "get_owner_customer_relation",
            ],
        )
        mock_ensure_owner_context.assert_awaited_once_with(context, fake_db)
        mock_update_owner_customer_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_user_id,
            relation_id=route_relation_id,
            update_data=expected_update_data,
        )
        mock_get_owner_customer_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_user_id,
            relation_id=updated_relation_id,
        )
        self.assertEqual(
            mock_get_owner_customer_relation.await_args.kwargs["relation_id"],
            updated_relation_id,
        )
        self.assertNotEqual(
            mock_get_owner_customer_relation.await_args.kwargs["relation_id"],
            route_relation_id,
        )
        mock_load_customer_relation_invitation_map.assert_not_called()
        mock_load_customer_relation_invitation_map.assert_not_awaited()
        mock_audit_log.assert_not_called()
        mock_serialize_customer_relation.assert_not_called()

    @patch("api.routers.customers.serialize_customer_relation", new_callable=MagicMock)
    @patch("api.routers.customers.audit_log", new_callable=MagicMock)
    @patch("api.routers.customers.load_customer_relation_invitation_map", new_callable=AsyncMock)
    @patch("api.routers.customers.get_owner_customer_relation", new_callable=AsyncMock)
    @patch("api.routers.customers.update_owner_customer_relation", new_callable=AsyncMock)
    @patch("api.routers.customers.ensure_owner_context", new_callable=AsyncMock)
    async def test_update_my_customer_invitation_map_failure(
        self,
        mock_ensure_owner_context: AsyncMock,
        mock_update_owner_customer_relation: AsyncMock,
        mock_get_owner_customer_relation: AsyncMock,
        mock_load_customer_relation_invitation_map: AsyncMock,
        mock_audit_log: MagicMock,
        mock_serialize_customer_relation: MagicMock,
    ) -> None:
        sentinel_error = RuntimeError(
            "load_customer_relation_invitation_map sentinel failure"
        )
        call_order: list[str] = []

        async def fake_ensure_owner_context(
            _context: object, _db: object
        ) -> None:
            call_order.append("ensure_owner_context")

        owner_user_id = 303
        route_relation_id = 601
        updated_relation_id = 602
        self.assertNotEqual(route_relation_id, updated_relation_id)

        stale_token = "token-stale-before-reload-111"
        reloaded_token = "token-distinctive-reloaded-999"
        self.assertNotEqual(stale_token, reloaded_token)

        stale_relation = SimpleNamespace(
            id=updated_relation_id,
            owner_user_id=owner_user_id,
            customer_user_id=789,
            customer_tier=CustomerTier.TIER_1,
            status="pending",
            invitation_token=stale_token,
        )

        reloaded_relation = SimpleNamespace(
            id=updated_relation_id,
            owner_user_id=owner_user_id,
            customer_user_id=789,
            customer_tier=CustomerTier.TIER_2,
            status="active",
            invitation_token=reloaded_token,
        )

        async def fake_update_owner_customer_relation(
            _db: object,
            *,
            owner_user_id: int,
            relation_id: int,
            update_data: dict[str, object],
        ) -> object:
            call_order.append("update_owner_customer_relation")
            return stale_relation

        async def fake_get_owner_customer_relation(
            _db: object,
            *,
            owner_user_id: int,
            relation_id: int,
        ) -> object:
            call_order.append("get_owner_customer_relation")
            return reloaded_relation

        async def fake_load_customer_relation_invitation_map(
            _db: object,
            _invitation_tokens: list[str],
        ) -> dict:
            call_order.append("load_customer_relation_invitation_map")
            raise sentinel_error

        mock_ensure_owner_context.side_effect = fake_ensure_owner_context
        mock_update_owner_customer_relation.side_effect = (
            fake_update_owner_customer_relation
        )
        mock_get_owner_customer_relation.side_effect = (
            fake_get_owner_customer_relation
        )
        mock_load_customer_relation_invitation_map.side_effect = (
            fake_load_customer_relation_invitation_map
        )

        context = SimpleNamespace(owner_user=SimpleNamespace(id=owner_user_id))
        fake_db = object()

        payload = schemas.CustomerRelationUpdate(
            customer_tier=CustomerTier.TIER_2,
            commission_rate=3.14,
            min_trade_quantity=20,
            max_trade_quantity=200,
            max_daily_trades=25,
            max_daily_commodity_volume=45000,
        )
        expected_update_data = payload.model_dump(exclude_unset=True)
        self.assertEqual(
            expected_update_data,
            {
                "customer_tier": CustomerTier.TIER_2,
                "commission_rate": 3.14,
                "min_trade_quantity": 20,
                "max_trade_quantity": 200,
                "max_daily_trades": 25,
                "max_daily_commodity_volume": 45000,
            },
        )

        with self.assertRaises(RuntimeError) as ctx:
            await update_my_customer(
                relation_id=route_relation_id,
                payload=payload,
                context=context,
                db=fake_db,
            )

        self.assertIs(ctx.exception, sentinel_error)
        self.assertEqual(
            call_order,
            [
                "ensure_owner_context",
                "update_owner_customer_relation",
                "get_owner_customer_relation",
                "load_customer_relation_invitation_map",
            ],
        )
        mock_ensure_owner_context.assert_awaited_once_with(context, fake_db)
        mock_update_owner_customer_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_user_id,
            relation_id=route_relation_id,
            update_data=expected_update_data,
        )
        mock_get_owner_customer_relation.assert_awaited_once_with(
            fake_db,
            owner_user_id=owner_user_id,
            relation_id=updated_relation_id,
        )
        mock_load_customer_relation_invitation_map.assert_awaited_once_with(
            fake_db,
            [reloaded_token],
        )
        self.assertEqual(
            mock_load_customer_relation_invitation_map.await_args.args,
            (fake_db, [reloaded_token]),
        )
        self.assertNotIn(
            stale_token,
            mock_load_customer_relation_invitation_map.await_args.args[1],
        )
        mock_audit_log.assert_not_called()
        mock_serialize_customer_relation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
