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
import models.user
from models.user import UserRole
from api.routers.users_public import read_public_user


class FailClosedAsyncSession:
    """Fail-closed custom async Session adapter for read_public_user ordinary lookup."""

    def __init__(
        self,
        expected_model: type,
        expected_id: int,
        target_user: object,
        *,
        events: list[tuple[object, ...]] | None = None,
    ) -> None:
        self._expected_model = expected_model
        self._expected_id = expected_id
        self._expected_key = (expected_model, expected_id)
        self._target_user = target_user
        self.events = events if events is not None else []
        self.get_calls: list[tuple[object, object]] = []

    async def get(self, entity: object, ident: object, *args: object, **kwargs: object) -> object:
        self.events.append(("db_get", entity, ident))
        self.get_calls.append((entity, ident))
        if entity is not self._expected_model or ident != self._expected_id:
            raise AssertionError(
                f"Unexpected db.get key {(entity, ident)!r}; expected {self._expected_key!r}"
            )
        return self._target_user

    async def execute(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("db.execute unexpectedly called on FailClosedAsyncSession")

    async def commit(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.commit unexpectedly called on FailClosedAsyncSession")

    async def rollback(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.rollback unexpectedly called on FailClosedAsyncSession")

    async def refresh(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.refresh unexpectedly called on FailClosedAsyncSession")

    async def scalar(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("db.scalar unexpectedly called on FailClosedAsyncSession")

    async def scalars(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("db.scalars unexpectedly called on FailClosedAsyncSession")


class UsersPublicReadOrdinaryNotFoundCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()

    async def test_ordinary_missing_or_deleted_target_falls_back_to_single_get_and_raises_exact_404(
        self,
    ) -> None:
        requested_user_id = 42
        current_user_id = 99

        current_user = SimpleNamespace(
            id=current_user_id,
            account_name="viewer_user_99",
            mobile_number="09129876543",
            avatar_file_id="avatar-viewer-99",
            address="نشانی بیننده",
            role=UserRole.STANDARD,
            is_deleted=False,
        )

        variants = [
            ("missing_user_none", None),
            (
                "soft_deleted_user",
                SimpleNamespace(
                    id=requested_user_id,
                    account_name="deleted_user_42",
                    mobile_number="09121112233",
                    avatar_file_id="avatar-target-42",
                    address="تهران، خیابان آزادی",
                    role=UserRole.STANDARD,
                    is_deleted=True,
                ),
            ),
        ]

        # Verify probe adapter rejects unexpected keys and forbidden operations
        probe_db = FailClosedAsyncSession(models.user.User, requested_user_id, None)
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
            await probe_db.refresh(None)
        with self.assertRaises(AssertionError):
            await probe_db.scalar()
        with self.assertRaises(AssertionError):
            await probe_db.scalars()

        for label, target_user in variants:
            with self.subTest(variant=label):
                events: list[tuple[object, ...]] = []
                db = FailClosedAsyncSession(
                    models.user.User,
                    requested_user_id,
                    target_user,
                    events=events,
                )

                async def accountant_lookup_side_effect(session: object, accountant_user_id: int):
                    if session is not db:
                        raise AssertionError(f"Expected session identity {db!r}, got {session!r}")
                    if accountant_user_id != requested_user_id:
                        raise AssertionError(
                            f"Expected accountant_user_id {requested_user_id!r}, got {accountant_user_id!r}"
                        )
                    events.append(("accountant_lookup", accountant_user_id))
                    return None

                async def customer_lookup_side_effect(session: object, customer_user_id: int):
                    if session is not db:
                        raise AssertionError(f"Expected session identity {db!r}, got {session!r}")
                    if customer_user_id != requested_user_id:
                        raise AssertionError(
                            f"Expected customer_user_id {requested_user_id!r}, got {customer_user_id!r}"
                        )
                    events.append(("customer_lookup", customer_user_id))
                    return None

                accountant_lookup_mock = AsyncMock(side_effect=accountant_lookup_side_effect)
                customer_lookup_mock = AsyncMock(side_effect=customer_lookup_side_effect)
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
                        ("db_get", models.user.User, requested_user_id),
                    ],
                )
                accountant_lookup_mock.assert_awaited_once_with(db, requested_user_id)
                customer_lookup_mock.assert_awaited_once_with(db, requested_user_id)
                self.assertEqual(db.get_calls, [(models.user.User, requested_user_id)])
                chat_targets_mock.assert_not_awaited()
                serializer_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
