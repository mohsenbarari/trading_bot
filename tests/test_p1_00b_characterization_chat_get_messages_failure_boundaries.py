import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from api.routers.chat import get_messages


class FakeScalarResult:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return self._values


class FakeExecuteResult:
    def __init__(self, *, scalars=None):
        self._scalars = list(scalars or [])

    def scalars(self):
        return FakeScalarResult(self._scalars)


class FakeDB:
    def __init__(self, *, scalar_results=None, execute_actions=None, call_log=None):
        self.scalar_results = list(scalar_results or [])
        self.execute_actions = list(execute_actions or [])
        self.scalar_calls = []
        self.execute_calls = []
        self.call_log = call_log if call_log is not None else []

    async def scalar(self, stmt):
        self.scalar_calls.append(stmt)
        self.call_log.append(("db.scalar", stmt))
        if self.scalar_results:
            result = self.scalar_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        raise AssertionError("Unexpected scalar() call when scalar_results is empty")

    async def execute(self, stmt):
        self.execute_calls.append(stmt)
        self.call_log.append(("db.execute", stmt))
        if self.execute_actions:
            action = self.execute_actions.pop(0)
            if isinstance(action, Exception):
                raise action
            if callable(action):
                return action(stmt)
            return action
        raise AssertionError("Unexpected execute() call when execute_actions is empty")


class CharacterizationChatGetMessagesFailureBoundariesTests(unittest.IsolatedAsyncioTestCase):
    async def test_builder_sentinel_exception_propagates_after_access_checks(self):
        call_order = []
        db = FakeDB(scalar_results=[42], call_log=call_order)
        current_user = SimpleNamespace(id=11)
        sentinel_error = RuntimeError("builder sentinel failure")

        async def record_ensure_access(*args, **kwargs):
            call_order.append("ensure_access")

        async def record_build_history(*args, **kwargs):
            call_order.append("build_history")
            raise sentinel_error

        ensure_mock = AsyncMock(side_effect=record_ensure_access)
        build_mock = AsyncMock(side_effect=record_build_history)
        serialize_mock = AsyncMock()

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
            with self.assertRaises(RuntimeError) as exc_info:
                await get_messages(
                    user_id=42,
                    limit=25,
                    before_id=99,
                    around_id=None,
                    current_user=current_user,
                    db=db,
                )

        self.assertIs(exc_info.exception, sentinel_error)
        self.assertEqual(len(db.scalar_calls), 1)
        self.assertEqual(db.scalar_results, [])
        self.assertEqual(len(db.execute_calls), 0)
        self.assertEqual(db.execute_actions, [])
        ensure_mock.assert_awaited_once_with(
            db,
            current_user=current_user,
            target_user_id=42,
        )
        build_mock.assert_awaited_once_with(
            db,
            current_user_id=11,
            other_user_id=42,
            limit=25,
            before_id=99,
            around_id=None,
        )
        serialize_mock.assert_not_called()
        self.assertEqual(
            [item[0] if isinstance(item, tuple) else item for item in call_order],
            ["db.scalar", "ensure_access", "build_history"],
        )

    async def test_around_id_newer_execute_sentinel_exception_propagates_without_row_extraction(self):
        call_order = []
        stmt_older = SimpleNamespace(name="stmt_older")
        stmt_newer = SimpleNamespace(name="stmt_newer")
        older_result = Mock(name="older_result")
        older_result.scalars = Mock(name="older_result.scalars")
        sentinel_error = RuntimeError("execute newer sentinel failure")

        db = FakeDB(
            scalar_results=[55],
            execute_actions=[older_result, sentinel_error],
            call_log=call_order,
        )
        current_user = SimpleNamespace(id=12)

        async def record_ensure_access(*args, **kwargs):
            call_order.append("ensure_access")

        async def record_build_history(*args, **kwargs):
            call_order.append("build_history")
            return stmt_older, stmt_newer

        ensure_mock = AsyncMock(side_effect=record_ensure_access)
        build_mock = AsyncMock(side_effect=record_build_history)
        serialize_mock = AsyncMock()

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
            with self.assertRaises(RuntimeError) as exc_info:
                await get_messages(
                    user_id=55,
                    limit=40,
                    around_id=700,
                    current_user=current_user,
                    db=db,
                )

        self.assertIs(exc_info.exception, sentinel_error)
        self.assertEqual(len(db.scalar_calls), 1)
        self.assertEqual(db.scalar_results, [])
        self.assertEqual(db.execute_actions, [])
        self.assertEqual(len(db.execute_calls), 2)
        self.assertIs(db.execute_calls[0], stmt_older)
        self.assertIs(db.execute_calls[1], stmt_newer)
        ensure_mock.assert_awaited_once_with(
            db,
            current_user=current_user,
            target_user_id=55,
        )
        build_mock.assert_awaited_once_with(
            db,
            current_user_id=12,
            other_user_id=55,
            limit=40,
            before_id=None,
            around_id=700,
        )
        older_result.scalars.assert_not_called()
        serialize_mock.assert_not_called()
        self.assertEqual(
            [item[0] if isinstance(item, tuple) else item for item in call_order],
            ["db.scalar", "ensure_access", "build_history", "db.execute", "db.execute"],
        )
        self.assertIs(call_order[3][1], stmt_older)
        self.assertIs(call_order[4][1], stmt_newer)

    async def test_default_path_serializer_sentinel_exception_propagates_with_reversed_message_identities(self):
        call_order = []
        stmt_older = SimpleNamespace(name="stmt_older_default")
        msg_300 = SimpleNamespace(id=300, body="msg_300")
        msg_200 = SimpleNamespace(id=200, body="msg_200")
        msg_100 = SimpleNamespace(id=100, body="msg_100")
        descending_messages = [msg_300, msg_200, msg_100]
        execute_result = FakeExecuteResult(scalars=descending_messages)
        sentinel_error = RuntimeError("serializer sentinel failure")

        db = FakeDB(
            scalar_results=[66],
            execute_actions=[execute_result],
            call_log=call_order,
        )
        current_user = SimpleNamespace(id=15)

        async def record_ensure_access(*args, **kwargs):
            call_order.append("ensure_access")

        async def record_build_history(*args, **kwargs):
            call_order.append("build_history")
            return stmt_older, None

        async def record_serialize(db_arg, messages, *, viewer_user_id=None):
            call_order.append("serialize")
            raise sentinel_error

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
            with self.assertRaises(RuntimeError) as exc_info:
                await get_messages(
                    user_id=66,
                    limit=30,
                    before_id=250,
                    around_id=None,
                    current_user=current_user,
                    db=db,
                )

        self.assertIs(exc_info.exception, sentinel_error)
        self.assertEqual(len(db.scalar_calls), 1)
        self.assertEqual(db.scalar_results, [])
        self.assertEqual(len(db.execute_calls), 1)
        self.assertEqual(db.execute_actions, [])
        self.assertIs(db.execute_calls[0], stmt_older)
        ensure_mock.assert_awaited_once_with(
            db,
            current_user=current_user,
            target_user_id=66,
        )
        build_mock.assert_awaited_once_with(
            db,
            current_user_id=15,
            other_user_id=66,
            limit=30,
            before_id=250,
            around_id=None,
        )
        serialize_mock.assert_awaited_once()
        serialize_args, serialize_kwargs = serialize_mock.await_args
        self.assertIs(serialize_args[0], db)
        passed_messages = serialize_args[1]
        self.assertEqual(len(passed_messages), 3)
        self.assertIs(passed_messages[0], msg_100)
        self.assertIs(passed_messages[1], msg_200)
        self.assertIs(passed_messages[2], msg_300)
        self.assertEqual(serialize_kwargs, {"viewer_user_id": 15})
        self.assertEqual(
            [item[0] if isinstance(item, tuple) else item for item in call_order],
            ["db.scalar", "ensure_access", "build_history", "db.execute", "serialize"],
        )


if __name__ == "__main__":
    unittest.main()
