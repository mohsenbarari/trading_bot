import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from api.routers.users_public import list_project_users_directory, search_public_users


class UsersPublicCustomerRelationLookupFailuresCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()
        self.initial_denied_connects = len(self.fence["denied_connects"])
        self.initial_live_redis = len(self.fence["live_redis_constructs"])
        self.current_user = SimpleNamespace(id=42)
        self.db = SimpleNamespace(
            execute=AsyncMock(side_effect=AssertionError("db.execute awaited unexpectedly")),
            commit=AsyncMock(side_effect=AssertionError("db.commit awaited unexpectedly")),
            rollback=AsyncMock(side_effect=AssertionError("db.rollback awaited unexpectedly")),
        )

    async def test_search_public_users_propagates_customer_relation_lookup_failure_before_query(self) -> None:
        sentinel = RuntimeError("search_public_users customer relation lookup failure sentinel")
        customer_relation_lookup_mock = AsyncMock(side_effect=sentinel)
        select_mock = Mock(side_effect=AssertionError("api.routers.users_public.select called unexpectedly"))
        allowed_targets_mock = AsyncMock(
            side_effect=AssertionError(
                "api.routers.users_public.build_allowed_customer_chat_targets awaited unexpectedly"
            )
        )

        with patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=customer_relation_lookup_mock,
        ), patch(
            "api.routers.users_public.select",
            new=select_mock,
        ), patch(
            "api.routers.users_public.build_allowed_customer_chat_targets",
            new=allowed_targets_mock,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await search_public_users(
                    q=None,
                    limit=25,
                    chat_targets=False,
                    db=self.db,
                    current_user=self.current_user,
                )

        self.assertIs(ctx.exception, sentinel)
        customer_relation_lookup_mock.assert_awaited_once_with(self.db, self.current_user.id)
        select_mock.assert_not_called()
        allowed_targets_mock.assert_not_awaited()
        self.db.execute.assert_not_awaited()
        self.db.commit.assert_not_awaited()
        self.db.rollback.assert_not_awaited()
        self.assertEqual(len(self.fence["denied_connects"]), self.initial_denied_connects)
        self.assertEqual(len(self.fence["live_redis_constructs"]), self.initial_live_redis)

    async def test_project_users_directory_propagates_customer_relation_lookup_failure_before_authorization(self) -> None:
        sentinel = RuntimeError("list_project_users_directory customer relation lookup failure sentinel")
        customer_relation_lookup_mock = AsyncMock(side_effect=sentinel)
        accountant_relation_mock = AsyncMock(
            side_effect=AssertionError(
                "api.routers.users_public.get_active_accountant_relation_for_accountant awaited unexpectedly"
            )
        )
        can_view_mock = Mock(
            side_effect=AssertionError(
                "api.routers.users_public._can_view_project_users_directory called unexpectedly"
            )
        )
        build_stmt_mock = Mock(
            side_effect=AssertionError(
                "api.routers.users_public._build_project_user_directory_stmt called unexpectedly"
            )
        )

        with patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=customer_relation_lookup_mock,
        ), patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=accountant_relation_mock,
        ), patch(
            "api.routers.users_public._can_view_project_users_directory",
            new=can_view_mock,
        ), patch(
            "api.routers.users_public._build_project_user_directory_stmt",
            new=build_stmt_mock,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await list_project_users_directory(
                    user_id=self.current_user.id,
                    q=None,
                    limit=25,
                    offset=0,
                    db=self.db,
                    current_user=self.current_user,
                )

        self.assertIs(ctx.exception, sentinel)
        customer_relation_lookup_mock.assert_awaited_once_with(self.db, self.current_user.id)
        accountant_relation_mock.assert_not_awaited()
        can_view_mock.assert_not_called()
        build_stmt_mock.assert_not_called()
        self.db.execute.assert_not_awaited()
        self.db.commit.assert_not_awaited()
        self.db.rollback.assert_not_awaited()
        self.assertEqual(len(self.fence["denied_connects"]), self.initial_denied_connects)
        self.assertEqual(len(self.fence["live_redis_constructs"]), self.initial_live_redis)


if __name__ == "__main__":
    unittest.main()
