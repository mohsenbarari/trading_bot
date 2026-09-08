"""Characterization test for ChannelBulkMemberAddRequest normalized-empty failure boundary."""

import unittest

from pydantic import ValidationError

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import ChannelBulkMemberAddRequest


class TestChannelBulkMemberAddNormalizedEmpty(unittest.TestCase):
    """Verify ChannelBulkMemberAddRequest raises ValidationError when all user IDs are filtered."""

    def test_nonpositive_and_duplicate_zero_user_ids_normalized_to_empty_raises_validation_error(self):
        with self.assertRaisesRegex(ValidationError, "No users selected"):
            ChannelBulkMemberAddRequest(
                user_ids=[0, -2, 0],
                select_all_active_users=False,
            )


if __name__ == "__main__":
    unittest.main()
