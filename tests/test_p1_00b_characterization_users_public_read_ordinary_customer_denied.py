"""Characterization test under Stage P1-00b and Assignment WA-P1-00B-FREEZE-20260904-001.

Freezes the read_public_user branch where an active customer viewer requests an existing ordinary user
whose id is not present in allowed customer chat targets, raising HTTPException(404, 'User not found').
This is current-behavior characterization only and does not update evidence/inventories,
resolve either canonical blocker (P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE or P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN),
or complete P1-00b.
"""

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
import schemas
from api.routers.users_public import _serialize_public_user, read_public_user
import models.user
from models.customer_relation import CustomerRelationStatus
from models.user import UserRole
from tests.test_p1_00b_characterization_users_public_read_ordinary_success import (
    FailClosedAsyncSession,
)


class UsersPublicReadOrdinaryCustomerDeniedCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()

    async def test_active_customer_viewer_disallowed_ordinary_target_raises_exact_404_with_exact_lookup_order(
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
        viewer_customer_relation = SimpleNamespace(
            id=501,
            owner_user_id=10,
            customer_user_id=current_user_id,
            status=CustomerRelationStatus.ACTIVE,
            deleted_at=None,
        )

        events: list[tuple[object, ...]] = []
        db = FailClosedAsyncSession(
            models.user.User,
            requested_user_id,
            target_user,
            events=events,
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
            if customer_user_id not in (requested_user_id, current_user_id):
                raise AssertionError(
                    f"Unexpected customer_user_id {customer_user_id!r}; expected {requested_user_id!r} or {current_user_id!r}"
                )
            events.append(("customer_lookup", customer_user_id))
            if customer_user_id == requested_user_id:
                return None
            return viewer_customer_relation

        async def chat_targets_side_effect(session: object, customer_user_id: int):
            if session is not db:
                raise AssertionError(f"Expected session identity {db!r}, got {session!r}")
            if customer_user_id != current_user_id:
                raise AssertionError(
                    f"Expected customer_user_id {current_user_id!r}, got {customer_user_id!r}"
                )
            events.append(("chat_targets_lookup", customer_user_id))
            return []

        serializer_spy = Mock(
            side_effect=AssertionError("_serialize_public_user called unexpectedly")
        )

        accountant_lookup_mock = AsyncMock(side_effect=accountant_lookup_side_effect)
        customer_lookup_mock = AsyncMock(side_effect=customer_lookup_side_effect)
        chat_targets_mock = AsyncMock(side_effect=chat_targets_side_effect)

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
                    user_id=requested_user_id,
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
                ("accountant_lookup", requested_user_id),
                ("customer_lookup", requested_user_id),
                ("db_get", models.user.User, requested_user_id),
                ("customer_lookup", current_user_id),
                ("chat_targets_lookup", current_user_id),
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
        chat_targets_mock.assert_awaited_once_with(db, current_user_id)
        self.assertEqual(db.get_calls, [(models.user.User, requested_user_id)])
        serializer_spy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
