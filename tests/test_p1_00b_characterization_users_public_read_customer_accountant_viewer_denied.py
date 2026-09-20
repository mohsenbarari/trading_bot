import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from fastapi import HTTPException
import schemas
from api.routers.users_public import _serialize_public_user, read_public_user
from models.accountant_relation import AccountantRelationStatus
from models.customer_relation import CustomerRelationStatus
from models.user import UserRole


class UsersPublicReadCustomerAccountantViewerDeniedCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()

    async def test_customer_target_is_hidden_from_accountant_viewer_for_different_owner_without_session_work(
        self,
    ) -> None:
        class _StrictHermeticSession:
            def execute(self, *args: object, **kwargs: object) -> None:
                raise AssertionError("db.execute called unexpectedly")

            def get(self, *args: object, **kwargs: object) -> None:
                raise AssertionError("db.get called unexpectedly")

            def commit(self, *args: object, **kwargs: object) -> None:
                raise AssertionError("db.commit called unexpectedly")

            def rollback(self, *args: object, **kwargs: object) -> None:
                raise AssertionError("db.rollback called unexpectedly")

        db = _StrictHermeticSession()
        current_user = SimpleNamespace(
            id=55,
            account_name="accountant_viewer_55",
            mobile_number="09123456755",
            avatar_file_id="avatar-accountant-55",
            address="تهران، میدان ونک",
            role=UserRole.STANDARD,
            is_deleted=False,
        )
        customer_user = SimpleNamespace(
            id=77,
            account_name="customer_user_77",
            mobile_number="09120000077",
            avatar_file_id="avatar-customer-77",
            address="اصفهان، میدان نقش جهان",
            role=UserRole.STANDARD,
            is_deleted=False,
        )
        customer_relation = SimpleNamespace(
            id=501,
            customer_user_id=77,
            owner_user_id=21,
            customer_user=customer_user,
            status=CustomerRelationStatus.ACTIVE,
            deleted_at=None,
        )
        viewer_accountant_relation = SimpleNamespace(
            id=301,
            accountant_user_id=55,
            owner_user_id=22,
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
        )

        events: list[tuple[str, int]] = []

        async def accountant_lookup_side_effect(session: object, accountant_user_id: int):
            if session is not db:
                raise AssertionError(f"Expected db identity {db!r}, got {session!r}")
            if accountant_user_id == 77:
                events.append(("accountant_lookup", accountant_user_id))
                return None
            if accountant_user_id == 55:
                events.append(("accountant_lookup", accountant_user_id))
                return viewer_accountant_relation
            raise AssertionError(f"Unexpected accountant_user_id {accountant_user_id!r}")

        async def customer_lookup_side_effect(session: object, customer_user_id: int):
            if session is not db:
                raise AssertionError(f"Expected db identity {db!r}, got {session!r}")
            if customer_user_id != 77:
                raise AssertionError(f"Expected customer_user_id 77, got {customer_user_id!r}")
            events.append(("customer_lookup", customer_user_id))
            return customer_relation

        def serializer_side_effect(user: object):
            raise AssertionError("_serialize_public_user called unexpectedly")

        accountant_lookup_mock = AsyncMock(side_effect=accountant_lookup_side_effect)
        customer_lookup_mock = AsyncMock(side_effect=customer_lookup_side_effect)
        chat_targets_mock = AsyncMock(
            side_effect=AssertionError("build_allowed_customer_chat_targets awaited unexpectedly")
        )
        serializer_spy = Mock(side_effect=serializer_side_effect)

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=accountant_lookup_mock,
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=customer_lookup_mock,
        ), patch(
            "api.routers.users_public.build_allowed_customer_chat_targets",
            new=chat_targets_mock,
        ), patch(
            "api.routers.users_public._serialize_public_user",
            new=serializer_spy,
        ):
            denied_connects_before = len(self.fence["denied_connects"])
            live_redis_before = len(self.fence["live_redis_constructs"])

            with self.assertRaises(HTTPException) as exc_info:
                await read_public_user(
                    user_id=77,
                    db=db,
                    current_user=current_user,
                )

            self.assertEqual(len(self.fence["denied_connects"]), denied_connects_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), live_redis_before)

        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "User not found")
        self.assertEqual(
            events,
            [
                ("accountant_lookup", 77),
                ("customer_lookup", 77),
                ("accountant_lookup", 55),
            ],
        )
        self.assertEqual(accountant_lookup_mock.await_count, 2)
        self.assertEqual(
            accountant_lookup_mock.await_args_list,
            [
                call(db, 77),
                call(db, 55),
            ],
        )
        customer_lookup_mock.assert_awaited_once_with(db, 77)
        chat_targets_mock.assert_not_awaited()
        serializer_spy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
