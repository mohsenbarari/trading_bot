"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This module does not update the executed-seed registry, reduce a canonical
blocker, promote evidence, or mark P1-00b complete.
"""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import ChannelUpdateRequest


class TestChannelUpdateRequestNormalization(unittest.TestCase):
    """Characterize successful whitespace normalization for ChannelUpdateRequest."""

    def test_channel_update_request_whitespace_normalized(self):
        request = ChannelUpdateRequest(
            title="   Announcements Channel   ",
            description="   Official announcements and broadcast channel.   ",
            avatar_file_id="   avatar-file-uuid-001   ",
        )

        self.assertEqual(request.title, "Announcements Channel")
        self.assertEqual(
            request.description, "Official announcements and broadcast channel."
        )
        self.assertEqual(request.avatar_file_id, "avatar-file-uuid-001")


if __name__ == "__main__":
    unittest.main()
