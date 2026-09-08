"""Characterization test for ChannelBulkMemberAddRequest select-all mode with nonpositive user IDs normalized to empty."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import ChannelBulkMemberAddRequest


class TestChannelBulkMemberAddSelectAllNormalizedEmpty(unittest.TestCase):
    """Verify ChannelBulkMemberAddRequest succeeds when select_all_active_users is True and user_ids normalizes to empty."""

    def test_select_all_active_users_with_normalized_empty_user_ids_succeeds(self):
        request = ChannelBulkMemberAddRequest(
            user_ids=[0, -2, 0],
            select_all_active_users=True,
        )
        self.assertEqual(request.user_ids, [])
        self.assertIs(request.select_all_active_users, True)


if __name__ == "__main__":
    unittest.main()
