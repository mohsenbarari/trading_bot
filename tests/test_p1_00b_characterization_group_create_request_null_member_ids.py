"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This module does not update the executed-seed registry, reduce a canonical
blocker, promote evidence, or mark P1-00b complete.
"""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from pydantic import ValidationError

from api.routers.chat_schemas import GroupCreateRequest


class TestGroupCreateRequestNullMemberIds(unittest.TestCase):
    """Characterize null member_ids failure boundary for GroupCreateRequest."""

    def test_group_create_request_null_member_ids(self):
        with self.assertRaises(ValidationError):
            GroupCreateRequest(title="Valid Title", member_ids=None)


if __name__ == "__main__":
    unittest.main()
