import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from api.routers.chat import get_messages
from models.user import User


class TrackedScalarResult:
    def __init__(self, values, *, name: str, call_log: list):
        self._values = list(values)
        self._name = name
        self._call_log = call_log

    def all(self):
        self._call_log.append(f"{self._name}.scalars.all")
        return list(self._values)


class TrackedExecuteResult:
    def __init__(self, *, scalars=None, name: str, call_log: list):
        self._scalars = list(scalars if scalars is not None else [])
        self._name = name
        self._call_log = call_log

    def scalars(self):
        return TrackedScalarResult(self._scalars, name=self._name, call_log=self._call_log)


class BoundedChatDbStub:
    def __init__(
        self,
        *,
        target_user_id: int,
        target_user_return_id: int | None = None,
        call_log: list | None = None,
    ):
        self.target_user_id = target_user_id
        self.target_user_return_id = (
            target_user_return_id if target_user_return_id is not None else target_user_id
        )
        self._execute_map = {}
        self.scalar_calls = []
        self.execute_calls = []
        self.call_log = call_log if call_log is not None else []

    def register_statement(self, stmt, result):
        self._execute_map[id(stmt)] = result

    async def scalar(self, stmt):
        self.scalar_calls.append(stmt)
        self.call_log.append(("db.scalar", stmt))
        expected_stmt = (
            select(User.id)
            .where(User.id == self.target_user_id, User.is_deleted.is_(False))
            .limit(1)
        )
        if self._statement_matches(stmt, expected_stmt):
            return self.target_user_return_id
        raise AssertionError(
            f"Unmatched scalar statement. Expected target user {self.target_user_id}, got: {stmt!r}"
        )

    def _statement_matches(self, actual, expected) -> bool:
        try:
            if actual.compare(expected):
                return True
        except Exception:
            pass
        actual_sql = str(actual)
        expected_sql = str(expected)
        return actual_sql == expected_sql and actual.compile().params == expected.compile().params

    async def execute(self, stmt):
        self.execute_calls.append(stmt)
        self.call_log.append(("db.execute", stmt))
        stmt_id = id(stmt)
        if stmt_id in self._execute_map:
            result = self._execute_map[stmt_id]
            if isinstance(result, Exception):
                raise result
            return result
        raise AssertionError(
            f"Unmatched execute statement with id {stmt_id}: {stmt!r}"
        )


class CharacterizeChatGetMessagesAroundSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_around_id_merges_older_and_newer_rows_in_ascending_order(self):
        call_order = []
        target_user_id = 42
        current_user = SimpleNamespace(id=10)
        db = BoundedChatDbStub(
            target_user_id=target_user_id,
            target_user_return_id=target_user_id,
            call_log=call_order,
        )

        stmt_older = SimpleNamespace(name="stmt_older_around")
        stmt_newer = SimpleNamespace(name="stmt_newer_around")

        msg_98 = SimpleNamespace(id=98, content="older-second")
        msg_99 = SimpleNamespace(id=99, content="older-first")
        msg_100 = SimpleNamespace(id=100, content="around-target")
        msg_101 = SimpleNamespace(id=101, content="newer-first")
        msg_102 = SimpleNamespace(id=102, content="newer-second")

        older_descending = [msg_99, msg_98]
        newer_ascending = [msg_100, msg_101, msg_102]

        res_older = TrackedExecuteResult(
            scalars=older_descending,
            name="res_older",
            call_log=call_order,
        )
        res_newer = TrackedExecuteResult(
            scalars=newer_ascending,
            name="res_newer",
            call_log=call_order,
        )

        db.register_statement(stmt_older, res_older)
        db.register_statement(stmt_newer, res_newer)

        serializer_sentinel = SimpleNamespace(token="serializer_success_sentinel_1")

        async def record_ensure_access(*args, **kwargs):
            call_order.append("ensure_access")

        async def record_build_history(*args, **kwargs):
            call_order.append("build_history")
            return stmt_older, stmt_newer

        async def record_serialize(*args, **kwargs):
            call_order.append("serialize")
            return serializer_sentinel

        ensure_mock = AsyncMock(side_effect=record_ensure_access)
        build_mock = AsyncMock(side_effect=record_build_history)
        serialize_mock = AsyncMock(side_effect=record_serialize)

        with patch(
            "api.routers.chat._ensure_customer_can_access_direct_target",
            new=ensure_mock,
        ), patch(
            "api.routers.chat.build_direct_message_history_statements",
            new=build_mock,
        ), patch(
            "api.routers.chat._serialize_direct_messages_with_accountant_contract",
            new=serialize_mock,
        ):
            response = await get_messages(
                user_id=target_user_id,
                limit=20,
                before_id=None,
                around_id=100,
                current_user=current_user,
                db=db,
            )

        self.assertIs(response, serializer_sentinel)

        self.assertEqual(len(db.scalar_calls), 1)
        expected_scalar_stmt = (
            select(User.id)
            .where(User.id == target_user_id, User.is_deleted.is_(False))
            .limit(1)
        )
        self.assertTrue(db.scalar_calls[0].compare(expected_scalar_stmt))

        ensure_mock.assert_awaited_once_with(
            db,
            current_user=current_user,
            target_user_id=target_user_id,
        )

        build_mock.assert_awaited_once_with(
            db,
            current_user_id=10,
            other_user_id=target_user_id,
            limit=20,
            before_id=None,
            around_id=100,
        )

        self.assertEqual(len(db.execute_calls), 2)
        self.assertIs(db.execute_calls[0], stmt_older)
        self.assertIs(db.execute_calls[1], stmt_newer)

        serialize_mock.assert_awaited_once()
        serialize_args, serialize_kwargs = serialize_mock.await_args
        self.assertIs(serialize_args[0], db)
        passed_messages = serialize_args[1]
        self.assertEqual(len(passed_messages), 5)
        self.assertIs(passed_messages[0], msg_98)
        self.assertIs(passed_messages[1], msg_99)
        self.assertIs(passed_messages[2], msg_100)
        self.assertIs(passed_messages[3], msg_101)
        self.assertIs(passed_messages[4], msg_102)
        self.assertEqual(serialize_kwargs, {"viewer_user_id": 10})

        self.assertEqual(
            [item[0] if isinstance(item, tuple) else item for item in call_order],
            [
                "db.scalar",
                "ensure_access",
                "build_history",
                "db.execute",
                "db.execute",
                "res_older.scalars.all",
                "res_newer.scalars.all",
                "serialize",
            ],
        )
        self.assertIs(call_order[3][1], stmt_older)
        self.assertIs(call_order[4][1], stmt_newer)

    async def test_around_id_with_empty_older_half_returns_newer_rows(self):
        call_order = []
        target_user_id = 77
        current_user = SimpleNamespace(id=25)
        db = BoundedChatDbStub(
            target_user_id=target_user_id,
            target_user_return_id=target_user_id,
            call_log=call_order,
        )

        stmt_older = SimpleNamespace(name="stmt_older_empty")
        stmt_newer = SimpleNamespace(name="stmt_newer_empty_older")

        msg_500 = SimpleNamespace(id=500, content="first-newer")
        msg_501 = SimpleNamespace(id=501, content="second-newer")

        older_descending = []
        newer_ascending = [msg_500, msg_501]

        res_older = TrackedExecuteResult(
            scalars=older_descending,
            name="res_older",
            call_log=call_order,
        )
        res_newer = TrackedExecuteResult(
            scalars=newer_ascending,
            name="res_newer",
            call_log=call_order,
        )

        db.register_statement(stmt_older, res_older)
        db.register_statement(stmt_newer, res_newer)

        serializer_sentinel = SimpleNamespace(token="serializer_success_sentinel_2")

        async def record_ensure_access(*args, **kwargs):
            call_order.append("ensure_access")

        async def record_build_history(*args, **kwargs):
            call_order.append("build_history")
            return stmt_older, stmt_newer

        async def record_serialize(*args, **kwargs):
            call_order.append("serialize")
            return serializer_sentinel

        ensure_mock = AsyncMock(side_effect=record_ensure_access)
        build_mock = AsyncMock(side_effect=record_build_history)
        serialize_mock = AsyncMock(side_effect=record_serialize)

        with patch(
            "api.routers.chat._ensure_customer_can_access_direct_target",
            new=ensure_mock,
        ), patch(
            "api.routers.chat.build_direct_message_history_statements",
            new=build_mock,
        ), patch(
            "api.routers.chat._serialize_direct_messages_with_accountant_contract",
            new=serialize_mock,
        ):
            response = await get_messages(
                user_id=target_user_id,
                limit=40,
                before_id=None,
                around_id=500,
                current_user=current_user,
                db=db,
            )

        self.assertIs(response, serializer_sentinel)

        self.assertEqual(len(db.scalar_calls), 1)
        expected_scalar_stmt = (
            select(User.id)
            .where(User.id == target_user_id, User.is_deleted.is_(False))
            .limit(1)
        )
        self.assertTrue(db.scalar_calls[0].compare(expected_scalar_stmt))

        ensure_mock.assert_awaited_once_with(
            db,
            current_user=current_user,
            target_user_id=target_user_id,
        )

        build_mock.assert_awaited_once_with(
            db,
            current_user_id=25,
            other_user_id=target_user_id,
            limit=40,
            before_id=None,
            around_id=500,
        )

        self.assertEqual(len(db.execute_calls), 2)
        self.assertIs(db.execute_calls[0], stmt_older)
        self.assertIs(db.execute_calls[1], stmt_newer)

        serialize_mock.assert_awaited_once()
        serialize_args, serialize_kwargs = serialize_mock.await_args
        self.assertIs(serialize_args[0], db)
        passed_messages = serialize_args[1]
        self.assertEqual(len(passed_messages), 2)
        self.assertIs(passed_messages[0], msg_500)
        self.assertIs(passed_messages[1], msg_501)
        self.assertEqual(serialize_kwargs, {"viewer_user_id": 25})

        self.assertEqual(
            [item[0] if isinstance(item, tuple) else item for item in call_order],
            [
                "db.scalar",
                "ensure_access",
                "build_history",
                "db.execute",
                "db.execute",
                "res_older.scalars.all",
                "res_newer.scalars.all",
                "serialize",
            ],
        )
        self.assertIs(call_order[3][1], stmt_older)
        self.assertIs(call_order[4][1], stmt_newer)


if __name__ == "__main__":
    unittest.main()
