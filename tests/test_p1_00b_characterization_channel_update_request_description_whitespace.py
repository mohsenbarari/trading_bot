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


class TestChannelUpdateRequestDescriptionWhitespace(unittest.TestCase):
    """Characterize ChannelUpdateRequest normalizing whitespace-only description to None."""

    def test_channel_update_request_whitespace_description_becomes_none(self):
        request = ChannelUpdateRequest(
            title="Valid Channel",
            description="   ",
        )
        self.assertIsNone(request.description)


if __name__ == "__main__":
    unittest.main()
