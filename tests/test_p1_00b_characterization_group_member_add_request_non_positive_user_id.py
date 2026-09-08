"""Characterization test for GroupMemberAddRequest non-positive user_id rejection boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import GroupMemberAddRequest


class TestGroupMemberAddRequestNonPositiveUserId(unittest.TestCase):
    """Verify GroupMemberAddRequest non-positive user_id rejection."""

    def test_group_member_add_request_non_positive_user_id_raises_validation_error(self):
        for invalid_user_id in (0, -1):
            with self.subTest(user_id=invalid_user_id):
                with self.assertRaises(pydantic.ValidationError) as cm:
                    GroupMemberAddRequest(user_id=invalid_user_id)
                has_expected_error = any(
                    err.get("loc") == ("user_id",)
                    and err.get("type") == "greater_than"
                    and err.get("ctx") == {"gt": 0}
                    for err in cm.exception.errors()
                )
                self.assertTrue(has_expected_error)


if __name__ == "__main__":
    unittest.main()
