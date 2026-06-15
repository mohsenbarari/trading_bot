import unittest
from types import SimpleNamespace

from scripts import dev_admin


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, *, execute_result=None, get_result=None):
        self.execute_result = execute_result
        self.get_result = get_result
        self.get_calls: list[tuple[object, int]] = []

    async def execute(self, _stmt):
        return FakeExecuteResult(self.execute_result)

    async def get(self, model, value):
        self.get_calls.append((model, value))
        return self.get_result


class DevAdminFindUserTests(unittest.IsolatedAsyncioTestCase):
    async def test_find_user_prefers_identity_fields_before_numeric_id_lookup(self):
        user = SimpleNamespace(id=77, mobile_number="09370809280")
        db = FakeDB(execute_result=user)

        result = await dev_admin.find_user(db, "09370809280")

        self.assertIs(result, user)
        self.assertEqual(db.get_calls, [])

    async def test_find_user_skips_int32_overflowing_numeric_identity(self):
        db = FakeDB(execute_result=None)

        result = await dev_admin.find_user(db, "09370809280")

        self.assertIsNone(result)
        self.assertEqual(db.get_calls, [])

    async def test_find_user_uses_primary_key_lookup_for_valid_integer_identity(self):
        user = SimpleNamespace(id=123)
        db = FakeDB(execute_result=None, get_result=user)

        result = await dev_admin.find_user(db, "123")

        self.assertIs(result, user)
        self.assertEqual(db.get_calls, [(dev_admin.User, 123)])


if __name__ == "__main__":
    unittest.main()
