"""Direct-characterization tests for api.routers.customers.get_my_customer_relation failure boundaries under P1-00b.

Characterizes failure boundaries under WA-P1-00B-FREEZE-20260904-001.
Gap: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-GET-CUSTOMERS-GET-MY-CUSTOMER-RELATION-L624
Target: api.routers.customers.get_my_customer_relation

This unit is test-only current-behavior freeze evidence and does not change runtime/product
behavior, promote inventory evidence, reduce the blocker, or close Stage P1-00b.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.customers import get_my_customer_relation  # noqa: E402


class CharacterizeGetMyCustomerRelationFailureBoundariesTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic unit tests characterizing get_my_customer_relation dependency failure boundaries."""

    @patch("api.routers.customers.serialize_customer_relation", new_callable=MagicMock)
    @patch("api.routers.customers.load_customer_relation_invitation_map", new_callable=AsyncMock)
    @patch("api.routers.customers.get_owner_customer_relation", new_callable=AsyncMock)
    @patch("api.routers.customers.ensure_owner_context", new_callable=AsyncMock)
    async def test_get_my_customer_relation_lookup_failure(
        self,
        mock_ensure_owner_context: AsyncMock,
        mock_get_owner_customer_relation: AsyncMock,
        mock_load_customer_relation_invitation_map: AsyncMock,
        mock_serialize_customer_relation: MagicMock,
    ) -> None:
        sentinel_error = RuntimeError("get_owner_customer_relation sentinel failure")
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
        context = SimpleNamespace(owner_user=SimpleNamespace(id=owner_user_id))
        fake_db = object()

        with self.assertRaises(RuntimeError) as ctx:
            await get_my_customer_relation(
                relation_id=relation_id,
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
        mock_load_customer_relation_invitation_map.assert_not_called()
        mock_load_customer_relation_invitation_map.assert_not_awaited()
        mock_serialize_customer_relation.assert_not_called()

    @patch("api.routers.customers.serialize_customer_relation", new_callable=MagicMock)
    @patch("api.routers.customers.load_customer_relation_invitation_map", new_callable=AsyncMock)
    @patch("api.routers.customers.get_owner_customer_relation", new_callable=AsyncMock)
    @patch("api.routers.customers.ensure_owner_context", new_callable=AsyncMock)
    async def test_get_my_customer_relation_invitation_map_failure(
        self,
        mock_ensure_owner_context: AsyncMock,
        mock_get_owner_customer_relation: AsyncMock,
        mock_load_customer_relation_invitation_map: AsyncMock,
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

        distinctive_token = "token-distinctive-relation-xyz-789"
        fake_relation = SimpleNamespace(invitation_token=distinctive_token)

        async def fake_get_owner_customer_relation(
            _db: object, *, owner_user_id: int, relation_id: int
        ) -> object:
            call_order.append("get_owner_customer_relation")
            return fake_relation

        async def fake_load_customer_relation_invitation_map(
            _db: object, _invitation_tokens: list[str]
        ) -> dict:
            call_order.append("load_customer_relation_invitation_map")
            raise sentinel_error

        mock_ensure_owner_context.side_effect = fake_ensure_owner_context
        mock_get_owner_customer_relation.side_effect = (
            fake_get_owner_customer_relation
        )
        mock_load_customer_relation_invitation_map.side_effect = (
            fake_load_customer_relation_invitation_map
        )

        owner_user_id = 42
        relation_id = 101
        context = SimpleNamespace(owner_user=SimpleNamespace(id=owner_user_id))
        fake_db = object()

        with self.assertRaises(RuntimeError) as ctx:
            await get_my_customer_relation(
                relation_id=relation_id,
                context=context,
                db=fake_db,
            )

        self.assertIs(ctx.exception, sentinel_error)
        self.assertEqual(
            call_order,
            [
                "ensure_owner_context",
                "get_owner_customer_relation",
                "load_customer_relation_invitation_map",
            ],
        )
        mock_ensure_owner_context.assert_awaited_once_with(context, fake_db)
        mock_get_owner_customer_relation.assert_awaited_once_with(
            fake_db, owner_user_id=owner_user_id, relation_id=relation_id
        )
        mock_load_customer_relation_invitation_map.assert_awaited_once_with(
            fake_db, [distinctive_token]
        )
        mock_serialize_customer_relation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
