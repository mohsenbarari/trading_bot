"""Characterization test for GroupMemberAddRequest missing user_id rejection boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import GroupMemberAddRequest


class TestGroupMemberAddRequestMissingUserId(unittest.TestCase):
    """Verify GroupMemberAddRequest missing user_id rejection."""

    def test_group_member_add_request_missing_user_id_raises_validation_error(self):
        with self.assertRaises(pydantic.ValidationError):
            GroupMemberAddRequest()


if __name__ == "__main__":
    unittest.main()
