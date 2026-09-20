"""Characterization test under Stage P1-00b and Assignment WA-P1-00B-FREEZE-20260904-001.

Freezes the read_public_user branch where an active customer viewer requests an accountant whose
live, non-deleted owner is present and the requested accountant ID is in the viewer's allowed
customer chat targets.
This direct characterization does not update the seed registry or inventories,
resolve P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE or P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN,
promote evidence, change product behavior, or complete P1-00b.
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

import schemas
from api.routers.users_public import _serialize_public_user, read_public_user
from models.accountant_relation import AccountantRelationStatus
from models.customer_relation import CustomerRelationStatus
from models.user import UserRole


class UsersPublicReadAccountantCustomerAllowedCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()

    async def test_active_customer_viewer_allowed_accountant_target_returns_owner_contact_with_exact_lookup_order(
        self,
    ) -> None:
        db = object()

        # Sentinel db object ensures no session operations are supported
        for op in ("execute", "get", "commit", "rollback", "refresh", "scalar", "scalars"):
            self.assertFalse(hasattr(db, op))

        requested_user_id = 44
        current_viewer_id = 99
        owner_id = 21

        owner_user = SimpleNamespace(
            id=owner_id,
            account_name="owner_user_21",
            mobile_number="09123456789",
            avatar_file_id="avatar-owner-21",
            address="تهران، میدان آزادی",
            is_deleted=False,
        )

        current_user = SimpleNamespace(
            id=current_viewer_id,
            account_name="customer_viewer_99",
            mobile_number="09129876543",
            avatar_file_id="avatar-viewer-99",
            address="نشانی بیننده",
            role=UserRole.STANDARD,
            is_deleted=False,
        )

        accountant_relation = SimpleNamespace(
            id=12,
            accountant_user_id=requested_user_id,
            owner_user_id=owner_id,
            owner_user=owner_user,
            status=AccountantRelationStatus.ACTIVE,
            deleted_at=None,
        )

        viewer_customer_relation = SimpleNamespace(
            id=101,
            owner_user_id=owner_id,
            customer_user_id=current_viewer_id,
            status=CustomerRelationStatus.ACTIVE,
            deleted_at=None,
        )

        events: list[tuple[str, int]] = []

        async def accountant_lookup_side_effect(session: object, accountant_user_id: int):
            if session is not db:
                raise AssertionError(f"Expected db identity {db!r}, got {session!r}")
            if accountant_user_id != requested_user_id:
                raise AssertionError(
                    f"Expected accountant_user_id {requested_user_id!r}, got {accountant_user_id!r}"
                )
            events.append(("accountant_lookup", accountant_user_id))
            return accountant_relation

        async def customer_lookup_side_effect(session: object, customer_user_id: int):
            if session is not db:
                raise AssertionError(f"Expected db identity {db!r}, got {session!r}")
            if customer_user_id != current_viewer_id:
                raise AssertionError(
                    f"Expected customer_user_id {current_viewer_id!r}, got {customer_user_id!r}"
                )
            events.append(("customer_lookup", customer_user_id))
            return viewer_customer_relation

        async def chat_targets_side_effect(session: object, customer_user_id: int):
            if session is not db:
                raise AssertionError(f"Expected db identity {db!r}, got {session!r}")
            if customer_user_id != current_viewer_id:
                raise AssertionError(
                    f"Expected customer_user_id {current_viewer_id!r}, got {customer_user_id!r}"
                )
            events.append(("chat_targets_lookup", customer_user_id))
            return [requested_user_id]

        def serializer_side_effect(user: object):
            if user is not owner_user:
                raise AssertionError(f"Expected owner_user identity {owner_user!r}, got {user!r}")
            events.append(("serialize_public_user", getattr(user, "id", None)))
            return _serialize_public_user(user)

        accountant_lookup_mock = AsyncMock(side_effect=accountant_lookup_side_effect)
        customer_lookup_mock = AsyncMock(side_effect=customer_lookup_side_effect)
        chat_targets_mock = AsyncMock(side_effect=chat_targets_side_effect)
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
                "id": owner_id,
                "account_name": "owner_user_21",
                "mobile_number": "09123456789",
                "avatar_file_id": "avatar-owner-21",
                "address": "تهران، میدان آزادی",
            },
        )
        self.assertEqual(
            events,
            [
                ("accountant_lookup", requested_user_id),
                ("customer_lookup", current_viewer_id),
                ("chat_targets_lookup", current_viewer_id),
                ("serialize_public_user", owner_id),
            ],
        )
        self.assertNotIn(("customer_lookup", requested_user_id), events)

        accountant_lookup_mock.assert_awaited_once_with(db, requested_user_id)
        customer_lookup_mock.assert_awaited_once_with(db, current_viewer_id)
        chat_targets_mock.assert_awaited_once_with(db, current_viewer_id)
        serializer_spy.assert_called_once_with(owner_user)
        self.assertIs(serializer_spy.call_args[0][0], owner_user)


if __name__ == "__main__":
    unittest.main()
