"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This module does not update the executed-seed registry, reduce blockers,
promote evidence, change product behavior, or mark P1-00b complete.
"""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import GroupCreateRequest


class TestGroupCreateRequestNormalization(unittest.TestCase):
    """Characterize combined whitespace and member_ids normalization for GroupCreateRequest."""

    def test_group_create_request_normalization(self):
        request = GroupCreateRequest(
            title="   Coordination Group   ",
            description="   Shared coordination room.   ",
            avatar_file_id="   avatar-file-uuid-017   ",
            member_ids=[7, 0, 7, -3, 2, 5, 2],
        )

        self.assertEqual(request.title, "Coordination Group")
        self.assertEqual(request.description, "Shared coordination room.")
        self.assertEqual(request.avatar_file_id, "avatar-file-uuid-017")
        self.assertEqual(request.member_ids, [7, 2, 5])


if __name__ == "__main__":
    unittest.main()
