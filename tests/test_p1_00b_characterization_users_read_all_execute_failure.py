"""Direct characterization test for api.routers.users.read_all_users execute failure.

Characterizes error propagation from db.execute before relation context enrichment
or serialization occurs.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import read_all_users


class UsersReadAllExecuteFailureCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_all_users_propagates_execute_failure_before_relation_context(self) -> None:
        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        sentinel_error = RuntimeError("db execute failure sentinel")
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=sentinel_error),
            commit=AsyncMock(side_effect=AssertionError("db.commit must not be awaited")),
            rollback=AsyncMock(side_effect=AssertionError("db.rollback must not be awaited")),
        )

        attach_relation_mock = AsyncMock(
            side_effect=AssertionError(
                "attach_user_management_relation_context must not be awaited when execute fails"
            )
        )
        serialize_mock = Mock(
            side_effect=AssertionError(
                "serialize_user_read must not be called when execute fails"
            )
        )

        with patch(
            "api.routers.users.attach_user_management_relation_context",
            new=attach_relation_mock,
        ), patch(
            "api.routers.users.serialize_user_read",
            new=serialize_mock,
        ):
            with self.assertRaises(RuntimeError) as exc_context:
                await read_all_users(
                    skip=0,
                    limit=100,
                    search=None,
                    include_deleted=False,
                    db=db,
                    actor=None,
                )

        self.assertIs(exc_context.exception, sentinel_error)
        db.execute.assert_awaited_once()
        attach_relation_mock.assert_not_awaited()
        serialize_mock.assert_not_called()
        db.commit.assert_not_awaited()
        db.rollback.assert_not_awaited()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
