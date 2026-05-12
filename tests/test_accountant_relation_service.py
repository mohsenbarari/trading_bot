import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.services.accountant_relation_service import (
    get_active_accountant_relation_for_accountant,
    get_effective_max_accountants,
    list_active_accountants_for_owner,
    resolve_effective_owner_actor,
    validate_accountant_capacity,
)


class FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


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

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class AccountantRelationServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_get_effective_max_accountants_clamps_invalid_values(self):
        self.assertEqual(get_effective_max_accountants(SimpleNamespace(max_accountants=5)), 5)
        self.assertEqual(get_effective_max_accountants(SimpleNamespace(max_accountants=-2)), 0)
        self.assertEqual(get_effective_max_accountants(SimpleNamespace(max_accountants="bad")), 3)
        self.assertEqual(get_effective_max_accountants(SimpleNamespace()), 3)

    async def test_get_active_accountant_relation_for_accountant_returns_active_relation(self):
        relation = SimpleNamespace(id=41, owner_user=SimpleNamespace(id=7), accountant_user=SimpleNamespace(id=9))
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=relation)])

        result = await get_active_accountant_relation_for_accountant(db, 9)

        self.assertIs(result, relation)

    async def test_list_active_accountants_for_owner_returns_rows(self):
        relation_one = SimpleNamespace(id=1)
        relation_two = SimpleNamespace(id=2)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[relation_one, relation_two])])

        result = await list_active_accountants_for_owner(db, 12)

        self.assertEqual(result, [relation_one, relation_two])

    async def test_validate_accountant_capacity_raises_when_owner_is_full(self):
        owner = SimpleNamespace(id=5, max_accountants=2)
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=2)])

        with self.assertRaises(HTTPException) as exc_info:
            await validate_accountant_capacity(db, owner)

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Owner has reached the maximum number of accountants")

    async def test_validate_accountant_capacity_returns_current_count_and_limit(self):
        owner = SimpleNamespace(id=5, max_accountants=4)
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_value=2)])

        current_count, limit = await validate_accountant_capacity(db, owner)

        self.assertEqual(current_count, 2)
        self.assertEqual(limit, 4)

    async def test_resolve_effective_owner_actor_returns_self_context_without_relation(self):
        user = SimpleNamespace(id=10)

        with patch(
            "core.services.accountant_relation_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ):
            context = await resolve_effective_owner_actor(FakeDB(), user)

        self.assertIs(context.owner_user, user)
        self.assertIs(context.actor_user, user)
        self.assertIsNone(context.relation)
        self.assertFalse(context.is_accountant_context)

    async def test_resolve_effective_owner_actor_returns_owner_context_for_accountant(self):
        owner = SimpleNamespace(id=2)
        actor = SimpleNamespace(id=7)
        relation = SimpleNamespace(owner_user=owner)

        with patch(
            "core.services.accountant_relation_service.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=relation),
        ):
            context = await resolve_effective_owner_actor(FakeDB(), actor)

        self.assertIs(context.owner_user, owner)
        self.assertIs(context.actor_user, actor)
        self.assertIs(context.relation, relation)
        self.assertTrue(context.is_accountant_context)


if __name__ == "__main__":
    unittest.main()