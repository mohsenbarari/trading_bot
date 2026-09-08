"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This direct schema characterization does not update the executed-seed registry,
reduce canonical blockers, promote evidence, or complete the Stage.
"""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import ChannelMemberUpdateRequest


class TestChannelMemberUpdateRemovalSuccess(unittest.TestCase):
    """Characterize successful channel member removal request."""

    def test_channel_member_removal_success(self):
        request = ChannelMemberUpdateRequest(role=None, remove_member=True)
        self.assertIsNone(request.role)
        self.assertIs(request.role, None)
        self.assertIs(request.remove_member, True)


if __name__ == "__main__":
    unittest.main()
