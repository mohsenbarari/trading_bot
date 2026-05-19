import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException

from core.services.customer_relation_service import (
    CUSTOMER_INVITATION_PREFIX,
    get_active_customer_relation_for_customer,
    get_effective_max_customers,
    get_pending_customer_relation_by_invitation_token,
    is_customer_invitation_token,
    is_user_customer,
    list_active_customers_for_owner,
    list_owner_customer_relations,
    sweep_expired_pending_customer_relations,
    validate_customer_capacity,
)
from models.customer_relation import CustomerRelation, CustomerRelationStatus


class FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values

    def first(self):
        return self._values[0] if self._values else None


class FakeExecuteResult:
    def __init__(self, values=None, scalar_one_value=None):
        self._values = values or []
        self._scalar_one_value = scalar_one_value

    def scalars(self):
        return FakeScalarResult(self._values)

    def scalar_one(self):
        return self._scalar_one_value

    def scalar_one_or_none(self):
        return self._scalar_one_value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.commit = AsyncMock()

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class CustomerRelationServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_customer_relation_status_and_tier_columns_use_database_values(self):
        self.assertEqual(
            CustomerRelation.__table__.c.status.type.enums,
            ["pending", "active", "expired", "revoked", "deleted"],
        )
        self.assertEqual(
            CustomerRelation.__table__.c.customer_tier.type.enums,
            ["tier1", "tier2"],
        )

    def test_get_effective_max_customers_clamps_invalid_values(self):
        self.assertEqual(get_effective_max_customers(SimpleNamespace(max_customers=6)), 6)
        self.assertEqual(get_effective_max_customers(SimpleNamespace(max_customers=-2)), 0)
        self.assertEqual(get_effective_max_customers(SimpleNamespace(max_customers="bad")), 5)
        self.assertEqual(get_effective_max_customers(SimpleNamespace()), 5)
        self.assertTrue(is_customer_invitation_token(f"{CUSTOMER_INVITATION_PREFIX}123"))
        self.assertFalse(is_customer_invitation_token("INV-123"))

    async def test_get_active_customer_relation_for_customer_returns_active_relation(self):
        relation = SimpleNamespace(id=41, owner_user=SimpleNamespace(id=7), customer_user=SimpleNamespace(id=9))
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=relation)])

        result = await get_active_customer_relation_for_customer(db, 9)

        self.assertIs(result, relation)

    async def test_list_active_customers_for_owner_returns_rows(self):
        relation_one = SimpleNamespace(id=1)
        relation_two = SimpleNamespace(id=2)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[relation_one, relation_two])])

        result = await list_active_customers_for_owner(db, 12)

        self.assertEqual(result, [relation_one, relation_two])

    async def test_sweep_expired_pending_customer_relations_marks_rows_deleted(self):
        expired = SimpleNamespace(
            status=CustomerRelationStatus.PENDING,
            deleted_at=None,
        )
        db = FakeDB(execute_results=[FakeExecuteResult(values=[expired])])

        expired_relations = await sweep_expired_pending_customer_relations(db)

        self.assertEqual(expired_relations, [expired])
        self.assertEqual(expired.status, CustomerRelationStatus.EXPIRED)
        self.assertIsNotNone(expired.deleted_at)

    async def test_get_pending_customer_relation_by_invitation_token_commits_expired_rows_before_lookup(self):
        pending_relation = SimpleNamespace(id=12)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[SimpleNamespace(status=CustomerRelationStatus.PENDING, deleted_at=None)]),
                FakeExecuteResult(scalar_one_value=pending_relation),
            ]
        )

        result = await get_pending_customer_relation_by_invitation_token(db, f"{CUSTOMER_INVITATION_PREFIX}token")

        self.assertIs(result, pending_relation)
        db.commit.assert_awaited_once()

    async def test_validate_customer_capacity_raises_when_owner_is_full(self):
        owner = SimpleNamespace(id=5, max_customers=2)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[]), FakeExecuteResult(scalar_one_value=2)])

        with self.assertRaises(HTTPException) as exc_info:
            await validate_customer_capacity(db, owner)

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Owner has reached the maximum number of customers")

    async def test_validate_customer_capacity_returns_current_count_and_limit(self):
        owner = SimpleNamespace(id=5, max_customers=4)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[]), FakeExecuteResult(scalar_one_value=2)])

        current_count, limit = await validate_customer_capacity(db, owner)

        self.assertEqual(current_count, 2)
        self.assertEqual(limit, 4)

    async def test_list_owner_customer_relations_commits_expired_rows_then_returns_pending_and_active(self):
        relation_one = SimpleNamespace(id=1)
        relation_two = SimpleNamespace(id=2)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[SimpleNamespace(status=CustomerRelationStatus.PENDING, deleted_at=None)]),
                FakeExecuteResult(values=[relation_one, relation_two]),
            ]
        )

        result = await list_owner_customer_relations(db, 7)

        self.assertEqual(result, [relation_one, relation_two])
        db.commit.assert_awaited_once()

    async def test_is_user_customer_delegates_to_active_relation_lookup(self):
        relation = SimpleNamespace(id=9)
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=relation)])

        self.assertTrue(await is_user_customer(db, 5))


if __name__ == "__main__":
    unittest.main()