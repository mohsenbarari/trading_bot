import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import schemas
from api.routers.users_public import _serialize_public_user, read_public_user
from models.customer_relation import CustomerRelationStatus
from models.user import UserRole


class UsersPublicReadCustomerOwnerCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()

    async def test_customer_target_returns_customer_contact_for_owner_without_viewer_accountant_or_session_work(
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
            id=21,
            account_name="owner_user_21",
            mobile_number="09123456789",
            avatar_file_id="avatar-owner-21",
            address="تهران، میدان آزادی",
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

        events: list[tuple[str, int]] = []

        async def accountant_lookup_side_effect(session: object, accountant_user_id: int):
            if session is not db:
                raise AssertionError(f"Expected db identity {db!r}, got {session!r}")
            if accountant_user_id == current_user.id:
                raise AssertionError(
                    f"Accountant lookup must not occur for current_user.id {current_user.id}"
                )
            if accountant_user_id != 77:
                raise AssertionError(f"Expected accountant_user_id 77, got {accountant_user_id!r}")
            events.append(("accountant_lookup", accountant_user_id))
            return None

        async def customer_lookup_side_effect(session: object, customer_user_id: int):
            if session is not db:
                raise AssertionError(f"Expected db identity {db!r}, got {session!r}")
            if customer_user_id != 77:
                raise AssertionError(f"Expected customer_user_id 77, got {customer_user_id!r}")
            events.append(("customer_lookup", customer_user_id))
            return customer_relation

        def serializer_side_effect(user: object):
            if user is not customer_user:
                raise AssertionError(f"Expected customer_user identity {customer_user!r}, got {user!r}")
            events.append(("serialize_public_user", getattr(user, "id", None)))
            return _serialize_public_user(user)

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

            result = await read_public_user(
                user_id=77,
                db=db,
                current_user=current_user,
            )

            self.assertEqual(len(self.fence["denied_connects"]), denied_connects_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), live_redis_before)

        self.assertIs(type(result), schemas.UserPublicRead)
        self.assertEqual(
            result.model_dump(),
            {
                "id": 77,
                "account_name": "customer_user_77",
                "mobile_number": "09120000077",
                "avatar_file_id": "avatar-customer-77",
                "address": "اصفهان، میدان نقش جهان",
            },
        )
        self.assertEqual(
            events,
            [
                ("accountant_lookup", 77),
                ("customer_lookup", 77),
                ("serialize_public_user", 77),
            ],
        )
        accountant_lookup_mock.assert_awaited_once_with(db, 77)
        customer_lookup_mock.assert_awaited_once_with(db, 77)
        chat_targets_mock.assert_not_awaited()
        serializer_spy.assert_called_once_with(customer_user)
        self.assertIs(serializer_spy.call_args[0][0], customer_user)


if __name__ == "__main__":
    unittest.main()
