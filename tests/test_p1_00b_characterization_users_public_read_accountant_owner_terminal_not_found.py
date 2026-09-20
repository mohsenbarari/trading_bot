"""Stage P1-00b characterization tests for read_public_user.

Assignment: WA-P1-00B-FREEZE-20260904-001
Behavior: API-IDENTITY-GET-USERS-PUBLIC-READ-PUBLIC-USER-L548
Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Direct-handler characterization for the terminal 404 outcome of
api.routers.users_public.read_public_user when the requested identity has an
active accountant relation whose owner_user is missing or soft-deleted, no active
customer relation exists for that requested identity, and the direct User fallback
lookup is either missing or soft-deleted.

This is direct-handler characterization and does not claim inventory reconciliation,
blocker closure, customer-viewer authorization coverage, persistence proof, or
Stage closure.
"""

import os

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
import models.user
from models.accountant_relation import AccountantRelationStatus
from models.user import UserRole
from api.routers.users_public import read_public_user
from tests.test_p1_00b_characterization_users_public_read_ordinary_success import (
    FailClosedAsyncSession,
)


class UsersPublicReadAccountantOwnerTerminalNotFoundCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()

    async def test_accountant_owner_and_requested_user_missing_or_deleted_raises_exact_terminal_404(
        self,
    ) -> None:
        requested_user_id = 42
        current_user_id = 99

        current_user = SimpleNamespace(
            id=current_user_id,
            account_name="viewer_user_99",
            full_name="Viewer User 99",
            mobile_number="09129876543",
            normalized_account_name="viewer_user_99",
            normalized_mobile_number="09129876543",
            avatar_file_id="avatar-viewer-99",
            address="نشانی بیننده",
            role=UserRole.STANDARD,
            is_deleted=False,
        )

        soft_deleted_owner = SimpleNamespace(
            id=21,
            account_name="deleted_owner_21",
            full_name="Deleted Owner 21",
            mobile_number="09123456789",
            normalized_account_name="deleted_owner_21",
            normalized_mobile_number="09123456789",
            avatar_file_id="avatar-owner-21",
            address="تهران، میدان آزادی",
            role=UserRole.STANDARD,
            is_deleted=True,
        )

        soft_deleted_target = SimpleNamespace(
            id=requested_user_id,
            account_name="deleted_user_42",
            full_name="Deleted Target 42",
            mobile_number="09121112233",
            normalized_account_name="deleted_user_42",
            normalized_mobile_number="09121112233",
            avatar_file_id="avatar-target-42",
            address="تهران، خیابان آزادی",
            role=UserRole.STANDARD,
            is_deleted=True,
        )

        # Validate probe adapter rejects wrong model/key plus execute, commit, rollback, refresh, scalar, and scalars
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

        matrix = [
            ("owner_none_target_none", None, None),
            ("owner_none_target_deleted", None, soft_deleted_target),
            ("owner_deleted_target_none", soft_deleted_owner, None),
            ("owner_deleted_target_deleted", soft_deleted_owner, soft_deleted_target),
        ]

        for variant_name, owner_user, target_user in matrix:
            with self.subTest(variant=variant_name):
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
                    status=AccountantRelationStatus.ACTIVE,
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
