import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import schemas
from api.routers.users_public import _serialize_public_user, read_public_user
import models.user
from models.user import UserRole
from tests.test_p1_00b_characterization_users_public_read_ordinary_success import (
    FailClosedAsyncSession,
)


class UsersPublicReadAccountantOwnerFallbackCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()

    async def test_missing_or_deleted_accountant_owner_falls_through_to_requested_user_contact_projection(
        self,
    ) -> None:
        requested_user_id = 42
        current_user_id = 99

        target_user = SimpleNamespace(
            id=requested_user_id,
            account_name="ordinary_user_42",
            mobile_number="09121112233",
            avatar_file_id="avatar-target-42",
            address="تهران، خیابان آزادی",
            role=UserRole.STANDARD,
            is_deleted=False,
        )
        current_user = SimpleNamespace(
            id=current_user_id,
            account_name="viewer_user_99",
            mobile_number="09129876543",
            avatar_file_id="avatar-viewer-99",
            address="نشانی بیننده",
            role=UserRole.STANDARD,
            is_deleted=False,
        )

        # Verify probe adapter rejects unexpected keys and forbidden operations
        probe_db = FailClosedAsyncSession(models.user.User, requested_user_id, target_user)
        with self.assertRaises(AssertionError):
            await probe_db.get(models.user.User, 999)
        with self.assertRaises(AssertionError):
            await probe_db.get(object, requested_user_id)
        with self.assertRaises(AssertionError):
            await probe_db.execute()
        with self.assertRaises(AssertionError):
            await probe_db.commit()
        with self.assertRaises(AssertionError):
            await probe_db.rollback()
        with self.assertRaises(AssertionError):
            await probe_db.refresh(target_user)
        with self.assertRaises(AssertionError):
            await probe_db.scalar()
        with self.assertRaises(AssertionError):
            await probe_db.scalars()

        scenarios = [
            ("owner_none", None),
            (
                "owner_deleted",
                SimpleNamespace(
                    id=21,
                    account_name="deleted_owner_21",
                    mobile_number="09123456789",
                    avatar_file_id="avatar-owner-21",
                    address="تهران، میدان آزادی",
                    role=UserRole.STANDARD,
                    is_deleted=True,
                ),
            ),
        ]

        for variant_name, owner_user in scenarios:
            with self.subTest(owner_variant=variant_name):
                events: list[tuple[object, ...]] = []
                db = FailClosedAsyncSession(
                    models.user.User,
                    requested_user_id,
                    target_user,
                    events=events,
                )

                accountant_relation = SimpleNamespace(
                    id=12,
                    accountant_user_id=requested_user_id,
                    owner_user_id=21,
                    owner_user=owner_user,
                    status="active",
                    deleted_at=None,
                )

                async def accountant_lookup_side_effect(session: object, accountant_user_id: int):
                    if session is not db:
                        raise AssertionError(f"Expected session identity {db!r}, got {session!r}")
                    if accountant_user_id != requested_user_id:
                        raise AssertionError(
                            f"Expected accountant_user_id {requested_user_id!r}, got {accountant_user_id!r}"
                        )
                    events.append(("accountant_lookup", accountant_user_id))
                    return accountant_relation

                async def customer_lookup_side_effect(session: object, customer_user_id: int):
                    if session is not db:
                        raise AssertionError(f"Expected session identity {db!r}, got {session!r}")
                    if customer_user_id not in (requested_user_id, current_user_id):
                        raise AssertionError(
                            f"Unexpected customer_user_id {customer_user_id!r}; expected {requested_user_id!r} or {current_user_id!r}"
                        )
                    events.append(("customer_lookup", customer_user_id))
                    return None

                def serializer_side_effect(user: object):
                    if user is not target_user:
                        raise AssertionError(f"Expected target_user identity {target_user!r}, got {user!r}")
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
                        user_id=requested_user_id,
                        db=db,
                        current_user=current_user,
                    )

                    self.assertEqual(len(self.fence["denied_connects"]), denied_connects_before)
                    self.assertEqual(len(self.fence["live_redis_constructs"]), live_redis_before)

                self.assertIs(type(result), schemas.UserPublicRead)
                self.assertEqual(
                    result.model_dump(),
                    {
                        "id": requested_user_id,
                        "account_name": "ordinary_user_42",
                        "mobile_number": "09121112233",
                        "avatar_file_id": "avatar-target-42",
                        "address": "تهران، خیابان آزادی",
                    },
                )
                self.assertEqual(
                    events,
                    [
                        ("accountant_lookup", requested_user_id),
                        ("customer_lookup", requested_user_id),
                        ("db_get", models.user.User, requested_user_id),
                        ("customer_lookup", current_user_id),
                        ("serialize_public_user", requested_user_id),
                    ],
                )
                accountant_lookup_mock.assert_awaited_once_with(db, requested_user_id)
                self.assertEqual(customer_lookup_mock.await_count, 2)
                self.assertEqual(
                    customer_lookup_mock.await_args_list,
                    [
                        unittest.mock.call(db, requested_user_id),
                        unittest.mock.call(db, current_user_id),
                    ],
                )
                self.assertEqual(db.get_calls, [(models.user.User, requested_user_id)])
                chat_targets_mock.assert_not_awaited()
                serializer_spy.assert_called_once_with(target_user)
                self.assertIs(serializer_spy.call_args[0][0], target_user)
                if owner_user is not None:
                    self.assertIsNot(serializer_spy.call_args[0][0], owner_user)
                    self.assertNotIn(owner_user, [call.args[0] for call in serializer_spy.call_args_list])


if __name__ == "__main__":
    unittest.main()
