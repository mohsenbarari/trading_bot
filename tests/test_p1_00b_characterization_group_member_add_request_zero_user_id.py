"""Characterization test for GroupMemberAddRequest zero user_id rejection boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import GroupMemberAddRequest


class TestGroupMemberAddRequestZeroUserId(unittest.TestCase):
    """Verify GroupMemberAddRequest zero user_id rejection."""

    def test_group_member_add_request_zero_user_id_raises_validation_error(self):
        with self.assertRaises(pydantic.ValidationError):
            GroupMemberAddRequest(user_id=0)


if __name__ == "__main__":
    unittest.main()
