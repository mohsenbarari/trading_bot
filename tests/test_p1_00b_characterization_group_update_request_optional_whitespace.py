"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This module does not update the executed-seed registry, reduce a canonical
blocker, promote evidence, or mark P1-00b complete.
"""

import unittest

from api.routers.chat_schemas import GroupUpdateRequest


class TestGroupUpdateRequestOptionalWhitespace(unittest.TestCase):
    """Characterize normalization of whitespace-only optional fields for GroupUpdateRequest."""

    def test_group_update_request_optional_whitespace_normalized_to_none(self):
        request = GroupUpdateRequest(
            title="   Team Alpha Discussion   ",
            description="   ",
            avatar_file_id="   ",
        )

        self.assertEqual(request.title, "Team Alpha Discussion")
        self.assertIsNone(request.description)
        self.assertIsNone(request.avatar_file_id)


if __name__ == "__main__":
    unittest.main()
