"""Characterization test for GroupMemberAddRequest positive user_id success path."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import GroupMemberAddRequest


class TestGroupMemberAddRequestPositiveUserId(unittest.TestCase):
    """Verify GroupMemberAddRequest positive user_id success path."""

    def test_group_member_add_request_positive_user_id_succeeds(self):
        request = GroupMemberAddRequest(user_id=7)
        self.assertEqual(request.user_id, 7)


if __name__ == "__main__":
    unittest.main()
