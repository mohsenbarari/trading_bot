"""Direct-characterization tests for api.routers.customers.list_my_customers failure boundaries under P1-00b.

Characterizes failure boundaries under WA-P1-00B-FREEZE-20260904-001.
Seed: API-IDENTITY-GET-CUSTOMERS-LIST-MY-CUSTOMERS-L386
Target: api.routers.customers.list_my_customers

This is direct characterization only and does not promote evidence, reduce the blocker,
or complete P1-00b.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.customers import list_my_customers  # noqa: E402


class CharacterizeListMyCustomersFailureBoundariesTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic unit tests characterizing list_my_customers dependency failure boundaries."""

    @patch("api.routers.customers.serialize_customer_relation", new_callable=MagicMock)
    @patch("api.routers.customers.load_invitation_sms_status_map", new_callable=AsyncMock)
    @patch("api.routers.customers.load_customer_relation_invitation_map", new_callable=AsyncMock)
    @patch("api.routers.customers.list_owner_customer_relations", new_callable=AsyncMock)
    @patch("api.routers.customers.ensure_owner_context", new_callable=AsyncMock)
    async def test_list_my_customers_ensure_owner_context_failure(
        self,
        mock_ensure_owner_context: AsyncMock,
        mock_list_owner_customer_relations: AsyncMock,
        mock_load_customer_relation_invitation_map: AsyncMock,
        mock_load_invitation_sms_status_map: AsyncMock,
        mock_serialize_customer_relation: MagicMock,
    ) -> None:
        sentinel_error = RuntimeError("ensure_owner_context sentinel failure")
        mock_ensure_owner_context.side_effect = sentinel_error

        context = SimpleNamespace(owner_user=SimpleNamespace(id=42))
        fake_db = object()

        with self.assertRaises(RuntimeError) as ctx:
            await list_my_customers(context=context, db=fake_db)

        self.assertIs(ctx.exception, sentinel_error)
        mock_ensure_owner_context.assert_awaited_once_with(context, fake_db)
        mock_list_owner_customer_relations.assert_not_called()
        mock_list_owner_customer_relations.assert_not_awaited()
        mock_load_customer_relation_invitation_map.assert_not_called()
        mock_load_customer_relation_invitation_map.assert_not_awaited()
        mock_load_invitation_sms_status_map.assert_not_called()
        mock_load_invitation_sms_status_map.assert_not_awaited()
        mock_serialize_customer_relation.assert_not_called()

    @patch("api.routers.customers.serialize_customer_relation", new_callable=MagicMock)
    @patch("api.routers.customers.load_invitation_sms_status_map", new_callable=AsyncMock)
    @patch("api.routers.customers.load_customer_relation_invitation_map", new_callable=AsyncMock)
    @patch("api.routers.customers.list_owner_customer_relations", new_callable=AsyncMock)
    @patch("api.routers.customers.ensure_owner_context", new_callable=AsyncMock)
    async def test_list_my_customers_relation_list_failure(
        self,
        mock_ensure_owner_context: AsyncMock,
        mock_list_owner_customer_relations: AsyncMock,
        mock_load_customer_relation_invitation_map: AsyncMock,
        mock_load_invitation_sms_status_map: AsyncMock,
        mock_serialize_customer_relation: MagicMock,
    ) -> None:
        sentinel_error = RuntimeError("list_owner_customer_relations sentinel failure")
        call_order: list[str] = []

        async def fake_ensure_owner_context(
            _context: object, _db: object
        ) -> None:
            call_order.append("ensure_owner_context")

        async def fake_list_owner_customer_relations(
            _db: object, *, owner_user_id: int
        ) -> list[object]:
            call_order.append("list_owner_customer_relations")
            raise sentinel_error

        mock_ensure_owner_context.side_effect = fake_ensure_owner_context
        mock_list_owner_customer_relations.side_effect = (
            fake_list_owner_customer_relations
        )

        owner_user_id = 42
        context = SimpleNamespace(owner_user=SimpleNamespace(id=owner_user_id))
        fake_db = object()

        with self.assertRaises(RuntimeError) as ctx:
            await list_my_customers(context=context, db=fake_db)

        self.assertIs(ctx.exception, sentinel_error)
        self.assertEqual(
            call_order,
            ["ensure_owner_context", "list_owner_customer_relations"],
        )
        mock_ensure_owner_context.assert_awaited_once_with(context, fake_db)
        mock_list_owner_customer_relations.assert_awaited_once_with(
            fake_db, owner_user_id=owner_user_id
        )
        mock_load_customer_relation_invitation_map.assert_not_called()
        mock_load_customer_relation_invitation_map.assert_not_awaited()
        mock_load_invitation_sms_status_map.assert_not_called()
        mock_load_invitation_sms_status_map.assert_not_awaited()
        mock_serialize_customer_relation.assert_not_called()

    @patch("api.routers.customers.serialize_customer_relation", new_callable=MagicMock)
    @patch("api.routers.customers.load_invitation_sms_status_map", new_callable=AsyncMock)
    @patch("api.routers.customers.load_customer_relation_invitation_map", new_callable=AsyncMock)
    @patch("api.routers.customers.list_owner_customer_relations", new_callable=AsyncMock)
    @patch("api.routers.customers.ensure_owner_context", new_callable=AsyncMock)
    async def test_list_my_customers_invitation_map_failure(
        self,
        mock_ensure_owner_context: AsyncMock,
        mock_list_owner_customer_relations: AsyncMock,
        mock_load_customer_relation_invitation_map: AsyncMock,
        mock_load_invitation_sms_status_map: AsyncMock,
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

        relation_a = SimpleNamespace(invitation_token="token-alpha-1")
        relation_b = SimpleNamespace(invitation_token="token-beta-2")
        relations = [relation_a, relation_b]
        expected_tokens = ["token-alpha-1", "token-beta-2"]

        async def fake_list_owner_customer_relations(
            _db: object, *, owner_user_id: int
        ) -> list[object]:
            call_order.append("list_owner_customer_relations")
            return relations

        async def fake_load_customer_relation_invitation_map(
            _db: object, _invitation_tokens: list[str]
        ) -> dict:
            call_order.append("load_customer_relation_invitation_map")
            raise sentinel_error

        mock_ensure_owner_context.side_effect = fake_ensure_owner_context
        mock_list_owner_customer_relations.side_effect = (
            fake_list_owner_customer_relations
        )
        mock_load_customer_relation_invitation_map.side_effect = (
            fake_load_customer_relation_invitation_map
        )

        owner_user_id = 42
        context = SimpleNamespace(owner_user=SimpleNamespace(id=owner_user_id))
        fake_db = object()

        with self.assertRaises(RuntimeError) as ctx:
            await list_my_customers(context=context, db=fake_db)

        self.assertIs(ctx.exception, sentinel_error)
        self.assertEqual(
            call_order,
            [
                "ensure_owner_context",
                "list_owner_customer_relations",
                "load_customer_relation_invitation_map",
            ],
        )
        mock_ensure_owner_context.assert_awaited_once_with(context, fake_db)
        mock_list_owner_customer_relations.assert_awaited_once_with(
            fake_db, owner_user_id=owner_user_id
        )
        mock_load_customer_relation_invitation_map.assert_awaited_once_with(
            fake_db, expected_tokens
        )
        mock_load_invitation_sms_status_map.assert_not_called()
        mock_load_invitation_sms_status_map.assert_not_awaited()
        mock_serialize_customer_relation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
