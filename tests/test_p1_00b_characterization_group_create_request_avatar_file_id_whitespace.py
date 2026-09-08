"""Characterization test for GroupCreateRequest avatar_file_id whitespace normalization."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import GroupCreateRequest


class TestGroupCreateRequestAvatarFileIdWhitespace(unittest.TestCase):
    """Verify GroupCreateRequest whitespace-only avatar_file_id normalization."""

    def test_group_create_request_whitespace_avatar_file_id_normalized_to_none(self):
        request = GroupCreateRequest(title="Valid Group", avatar_file_id="   ")
        self.assertIsNone(request.avatar_file_id)


if __name__ == "__main__":
    unittest.main()
