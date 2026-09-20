import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from api.routers.users_public import read_public_user
from models.customer_relation import CustomerRelationStatus
from models.user import User, UserRole


class FailClosedAsyncSession:
    """Hermetic fail-closed AsyncSession verifying no direct DB operations occur."""

    def __init__(self) -> None:
        self.get_calls: list[tuple[object, ...]] = []
        self.execute_calls: list[tuple[object, ...]] = []
        self.commit_calls: list[tuple[object, ...]] = []
        self.rollback_calls: list[tuple[object, ...]] = []

    async def get(self, *args: object, **kwargs: object) -> object:
        self.get_calls.append(args)
        raise AssertionError("db.get unexpectedly called on FailClosedAsyncSession")

    async def execute(self, *args: object, **kwargs: object) -> object:
        self.execute_calls.append(args)
        raise AssertionError("db.execute unexpectedly called on FailClosedAsyncSession")

    async def commit(self, *args: object, **kwargs: object) -> None:
        self.commit_calls.append(args)
        raise AssertionError("db.commit unexpectedly called on FailClosedAsyncSession")

    async def rollback(self, *args: object, **kwargs: object) -> None:
        self.rollback_calls.append(args)
        raise AssertionError("db.rollback unexpectedly called on FailClosedAsyncSession")

    async def refresh(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.refresh unexpectedly called on FailClosedAsyncSession")

    async def scalar(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("db.scalar unexpectedly called on FailClosedAsyncSession")

    async def scalars(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("db.scalars unexpectedly called on FailClosedAsyncSession")


class UsersPublicReadCustomerNotFoundCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()

    async def test_customer_relation_missing_or_deleted_customer_raises_exact_404_before_viewer_accountant_or_session_work(
        self,
    ) -> None:
        requested_user_id = 77
        viewer_user_id = 55

        # Verify probe adapter rejects forbidden operations
        probe_db = FailClosedAsyncSession()
        with self.assertRaises(AssertionError):
            await probe_db.execute()
        with self.assertRaises(AssertionError):
            await probe_db.get(User, requested_user_id)
        with self.assertRaises(AssertionError):
            await probe_db.commit()
        with self.assertRaises(AssertionError):
            await probe_db.rollback()
        with self.assertRaises(AssertionError):
            await probe_db.refresh(None)
        with self.assertRaises(AssertionError):
            await probe_db.scalar()
        with self.assertRaises(AssertionError):
            await probe_db.scalars()

        current_user = SimpleNamespace(
            id=viewer_user_id,
            account_name="viewer_user_55",
            mobile_number="09123456755",
            avatar_file_id="avatar-viewer-55",
            address="تهران، خیابان ولیعصر",
            role=UserRole.STANDARD,
            is_deleted=False,
        )

        variants = [
            ("customer_user_none", None),
            (
                "customer_user_deleted",
                SimpleNamespace(
                    id=requested_user_id,
                    account_name="customer_user_77",
                    mobile_number="09120000077",
                    avatar_file_id="avatar-customer-77",
                    address="اصفهان، میدان نقش جهان",
                    role=UserRole.STANDARD,
                    is_deleted=True,
                ),
            ),
        ]

        for label, customer_user in variants:
            with self.subTest(variant=label):
                db = FailClosedAsyncSession()
                customer_relation = SimpleNamespace(
                    id=501,
                    customer_user_id=requested_user_id,
                    owner_user_id=12,
                    customer_user=customer_user,
                    status=CustomerRelationStatus.ACTIVE,
                    deleted_at=None,
                )

                self.assertIs(customer_relation.customer_user, customer_user)
                if customer_user is not None:
                    self.assertTrue(customer_user.is_deleted)
                    self.assertEqual(customer_user.id, requested_user_id)
                else:
                    self.assertIsNone(customer_relation.customer_user)

                events: list[tuple[str, int]] = []

                async def accountant_lookup_side_effect(session: object, accountant_user_id: int):
                    if session is not db:
                        raise AssertionError(f"Expected db identity {db!r}, got {session!r}")
                    if accountant_user_id == viewer_user_id:
                        raise AssertionError(
                            f"Accountant lookup must not occur for viewer ID {viewer_user_id}"
                        )
                    if accountant_user_id != requested_user_id:
                        raise AssertionError(
                            f"Expected accountant_user_id {requested_user_id!r}, got {accountant_user_id!r}"
                        )
                    events.append(("accountant_lookup", accountant_user_id))
                    return None

                async def customer_lookup_side_effect(session: object, customer_user_id: int):
                    if session is not db:
                        raise AssertionError(f"Expected db identity {db!r}, got {session!r}")
                    if customer_user_id != requested_user_id:
                        raise AssertionError(
                            f"Expected customer_user_id {requested_user_id!r}, got {customer_user_id!r}"
                        )
                    events.append(("customer_lookup", customer_user_id))
                    return customer_relation

                accountant_lookup_mock = AsyncMock(side_effect=accountant_lookup_side_effect)
                customer_lookup_mock = AsyncMock(side_effect=customer_lookup_side_effect)
                immediate_access_mock = Mock(
                    side_effect=AssertionError("_customer_profile_access_is_immediate called unexpectedly")
                )
                can_view_mock = Mock(
                    side_effect=AssertionError("_can_view_customer_profile called unexpectedly")
                )
                chat_targets_mock = AsyncMock(
                    side_effect=AssertionError("build_allowed_customer_chat_targets awaited unexpectedly")
                )
                serializer_mock = Mock(
                    side_effect=AssertionError("_serialize_public_user called unexpectedly")
                )

                with patch(
                    "api.routers.users_public.get_active_accountant_relation_for_accountant",
                    new=accountant_lookup_mock,
                ), patch(
                    "api.routers.users_public.get_active_customer_relation_for_customer",
                    new=customer_lookup_mock,
                ), patch(
                    "api.routers.users_public._customer_profile_access_is_immediate",
                    new=immediate_access_mock,
                ), patch(
                    "api.routers.users_public._can_view_customer_profile",
                    new=can_view_mock,
                ), patch(
                    "api.routers.users_public.build_allowed_customer_chat_targets",
                    new=chat_targets_mock,
                ), patch(
                    "api.routers.users_public._serialize_public_user",
                    new=serializer_mock,
                ):
                    denied_connects_before = len(self.fence["denied_connects"])
                    live_redis_before = len(self.fence["live_redis_constructs"])

                    with self.assertRaises(HTTPException) as exc_info:
                        await read_public_user(
                            user_id=requested_user_id,
                            db=db,
                            current_user=current_user,
                        )

                    self.assertEqual(exc_info.exception.status_code, 404)
                    self.assertEqual(exc_info.exception.detail, "User not found")
                    self.assertEqual(len(self.fence["denied_connects"]), denied_connects_before)
                    self.assertEqual(len(self.fence["live_redis_constructs"]), live_redis_before)

                self.assertEqual(
                    events,
                    [
                        ("accountant_lookup", requested_user_id),
                        ("customer_lookup", requested_user_id),
                    ],
                )
                accountant_lookup_mock.assert_awaited_once_with(db, requested_user_id)
                customer_lookup_mock.assert_awaited_once_with(db, requested_user_id)
                self.assertNotIn(("accountant_lookup", viewer_user_id), events)
                immediate_access_mock.assert_not_called()
                can_view_mock.assert_not_called()
                chat_targets_mock.assert_not_awaited()
                serializer_mock.assert_not_called()
                self.assertEqual(db.execute_calls, [])
                self.assertEqual(db.get_calls, [])
                self.assertEqual(db.commit_calls, [])
                self.assertEqual(db.rollback_calls, [])


if __name__ == "__main__":
    unittest.main()
