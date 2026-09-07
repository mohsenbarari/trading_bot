"""Assignment guard: characterization test for ChannelUpdateRequest avatar_file_id whitespace normalization."""

from __future__ import annotations

import unittest

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from api.routers.chat_schemas import ChannelUpdateRequest


class ChannelUpdateRequestAvatarWhitespaceCharacterizationTest(unittest.TestCase):
    """Assignment guard: verify ChannelUpdateRequest whitespace-only avatar_file_id normalization."""

    def test_whitespace_avatar_file_id_normalized_to_none(self):
        """Assert that whitespace-only avatar_file_id is normalized to None."""
        request = ChannelUpdateRequest(title="Valid Channel", avatar_file_id="   ")
        self.assertIsNone(request.avatar_file_id)


if __name__ == "__main__":
    unittest.main()
