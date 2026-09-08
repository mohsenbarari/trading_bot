"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This module does not update the executed-seed registry, reduce a canonical
blocker, promote evidence, or mark P1-00b complete.
"""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import GroupCreateRequest


class TestGroupCreateRequestMemberIdsNormalization(unittest.TestCase):
    """Characterize member_ids normalization for GroupCreateRequest."""

    def test_group_create_request_member_ids_normalization(self):
        request = GroupCreateRequest(
            title="Valid Title",
            member_ids=[7, 0, 7, -3, 2, 5, 2],
        )
        self.assertEqual(request.member_ids, [7, 2, 5])


if __name__ == "__main__":
    unittest.main()
