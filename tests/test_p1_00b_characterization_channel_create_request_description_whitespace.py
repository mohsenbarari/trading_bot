"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This module does not update the executed-seed registry, reduce a canonical
blocker, promote evidence, change product behavior, or mark P1-00b complete.
"""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import ChannelCreateRequest


class TestChannelCreateRequestDescriptionWhitespace(unittest.TestCase):
    """Characterize normalization of whitespace-only description for ChannelCreateRequest."""

    def test_channel_create_request_whitespace_description_normalized_to_none(self):
        request = ChannelCreateRequest(title="General", description="   ")
        self.assertIsNone(request.description)


if __name__ == "__main__":
    unittest.main()
