"""Characterization test for ChannelBulkMemberAddRequest positive-ID filtering and stable deduplication."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import ChannelBulkMemberAddRequest


class TestChannelBulkMemberAddNormalization(unittest.TestCase):
    """Verify ChannelBulkMemberAddRequest normalizes user IDs with positive filtering and stable deduplication."""

    def test_positive_id_filtering_and_stable_deduplication(self):
        request = ChannelBulkMemberAddRequest(
            user_ids=[9, 0, 3, 9, -2, 5, 3],
            select_all_active_users=False,
        )
        self.assertEqual(request.user_ids, [9, 3, 5])
        self.assertIs(request.select_all_active_users, False)


if __name__ == "__main__":
    unittest.main()
