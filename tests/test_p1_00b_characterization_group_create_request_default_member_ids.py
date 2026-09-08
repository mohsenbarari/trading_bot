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


class TestGroupCreateRequestDefaultMemberIds(unittest.TestCase):
    """Characterize default member_ids behavior for GroupCreateRequest."""

    def test_group_create_request_default_member_ids(self):
        req1 = GroupCreateRequest(title="Valid Title")
        self.assertEqual(req1.member_ids, [])

        req2 = GroupCreateRequest(title="Another Valid Title")
        self.assertEqual(req2.member_ids, [])
        self.assertIsNot(req1.member_ids, req2.member_ids)


if __name__ == "__main__":
    unittest.main()
