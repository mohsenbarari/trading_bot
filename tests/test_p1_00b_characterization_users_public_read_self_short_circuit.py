import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import schemas
from api.routers.users_public import _serialize_public_user, read_public_user


class UsersPublicReadSelfShortCircuitCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()

    async def test_self_request_returns_contact_projection_before_relation_or_session_work(self) -> None:
        current_user = SimpleNamespace(
            id=73,
            account_name="self-owner",
            mobile_number="09120000073",
            avatar_file_id="self-avatar-file-id",
            address="نشانی خود",
        )
        db = object()
        serializer_spy = Mock(wraps=_serialize_public_user)
        accountant_relation_mock = AsyncMock(
            side_effect=AssertionError(
                "api.routers.users_public.get_active_accountant_relation_for_accountant awaited unexpectedly"
            )
        )

        with patch(
            "api.routers.users_public._serialize_public_user",
            new=serializer_spy,
        ), patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=accountant_relation_mock,
        ):
            denied_connects_before = len(self.fence["denied_connects"])
            live_redis_before = len(self.fence["live_redis_constructs"])

            result = await read_public_user(
                user_id=current_user.id,
                db=db,
                current_user=current_user,
            )

            self.assertEqual(len(self.fence["denied_connects"]), denied_connects_before)
            self.assertEqual(len(self.fence["live_redis_constructs"]), live_redis_before)

        self.assertIs(type(result), schemas.UserPublicRead)
        self.assertEqual(
            result.model_dump(),
            {
                "id": 73,
                "account_name": "self-owner",
                "mobile_number": "09120000073",
                "avatar_file_id": "self-avatar-file-id",
                "address": "نشانی خود",
            },
        )
        serializer_spy.assert_called_once_with(current_user)
        self.assertIs(serializer_spy.call_args[0][0], current_user)
        accountant_relation_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
