"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This module does not update the executed-seed registry, reduce canonical
blockers, promote evidence, or complete the Stage.
"""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from pydantic import ValidationError

from api.routers.chat_schemas import GroupCreateRequest


class TestGroupCreateRequestNonNumericMemberId(unittest.TestCase):
    """Characterize nonnumeric member_ids item rejection boundary for GroupCreateRequest."""

    def test_group_create_request_non_numeric_member_id(self):
        with self.assertRaises(ValidationError):
            GroupCreateRequest(title="Valid Title", member_ids=[7, "not-an-id", 2])


if __name__ == "__main__":
    unittest.main()
