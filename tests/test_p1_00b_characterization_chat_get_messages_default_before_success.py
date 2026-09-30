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


class CharacterizeChatGetMessagesDefaultBeforeSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_history_reverses_rows_and_returns_serializer_result(self):
        call_order = []
        target_user_id = 42
        current_user = SimpleNamespace(id=10)
        db = BoundedChatDbStub(
            target_user_id=target_user_id,
            target_user_return_id=target_user_id,
            call_log=call_order,
        )

        stmt_older = SimpleNamespace(name="stmt_older_default")

        msg_300 = SimpleNamespace(id=300, content="msg-300-newer-descending")
        msg_200 = SimpleNamespace(id=200, content="msg-200-middle-descending")
        msg_100 = SimpleNamespace(id=100, content="msg-100-older-descending")
        descending_rows = [msg_300, msg_200, msg_100]

        res_older = TrackedExecuteResult(
            scalars=descending_rows,
            name="res_older",
            call_log=call_order,
        )
        db.register_statement(stmt_older, res_older)

        serializer_sentinel = SimpleNamespace(token="serializer_sentinel_default")

        async def record_ensure_access(*args, **kwargs):
            call_order.append("ensure_access")

        async def record_build_history(*args, **kwargs):
            call_order.append("build_history")
            return stmt_older, None

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
                around_id=None,
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
            around_id=None,
        )

        self.assertEqual(len(db.execute_calls), 1)
        self.assertIs(db.execute_calls[0], stmt_older)

        serialize_mock.assert_awaited_once()
        serialize_args, serialize_kwargs = serialize_mock.await_args
        self.assertIs(serialize_args[0], db)
        passed_messages = serialize_args[1]
        self.assertEqual(len(passed_messages), 3)
        self.assertIs(passed_messages[0], msg_100)
        self.assertIs(passed_messages[1], msg_200)
        self.assertIs(passed_messages[2], msg_300)
        self.assertEqual(serialize_kwargs, {"viewer_user_id": 10})

        self.assertEqual(
            [item[0] if isinstance(item, tuple) else item for item in call_order],
            [
                "db.scalar",
                "ensure_access",
                "build_history",
                "db.execute",
                "res_older.scalars.all",
                "serialize",
            ],
        )
        self.assertIs(call_order[3][1], stmt_older)

    async def test_before_id_empty_history_executes_once_and_returns_serializer_result(self):
        call_order = []
        target_user_id = 77
        current_user = SimpleNamespace(id=25)
        db = BoundedChatDbStub(
            target_user_id=target_user_id,
            target_user_return_id=target_user_id,
            call_log=call_order,
        )

        stmt_older = SimpleNamespace(name="stmt_older_before")
        res_older = TrackedExecuteResult(
            scalars=[],
            name="res_older",
            call_log=call_order,
        )
        db.register_statement(stmt_older, res_older)

        serializer_sentinel = SimpleNamespace(token="serializer_sentinel_before_empty")

        async def record_ensure_access(*args, **kwargs):
            call_order.append("ensure_access")

        async def record_build_history(*args, **kwargs):
            call_order.append("build_history")
            return stmt_older, None

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
                limit=30,
                before_id=99,
                around_id=None,
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
            limit=30,
            before_id=99,
            around_id=None,
        )

        self.assertEqual(len(db.execute_calls), 1)
        self.assertIs(db.execute_calls[0], stmt_older)

        serialize_mock.assert_awaited_once()
        serialize_args, serialize_kwargs = serialize_mock.await_args
        self.assertIs(serialize_args[0], db)
        passed_messages = serialize_args[1]
        self.assertEqual(passed_messages, [])
        self.assertEqual(serialize_kwargs, {"viewer_user_id": 25})

        self.assertEqual(
            [item[0] if isinstance(item, tuple) else item for item in call_order],
            [
                "db.scalar",
                "ensure_access",
                "build_history",
                "db.execute",
                "res_older.scalars.all",
                "serialize",
            ],
        )
        self.assertIs(call_order[3][1], stmt_older)


if __name__ == "__main__":
    unittest.main()
