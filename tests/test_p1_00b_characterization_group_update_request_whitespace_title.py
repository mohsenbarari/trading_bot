"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This module does not update the executed-seed registry, reduce a canonical
blocker, promote evidence, or mark P1-00b complete.
"""

import unittest

from pydantic import ValidationError

from api.routers.chat_schemas import GroupUpdateRequest


class TestGroupUpdateRequestWhitespaceTitle(unittest.TestCase):
    """Characterize rejection of whitespace-only title for GroupUpdateRequest."""

    def test_group_update_request_whitespace_title_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            GroupUpdateRequest(title="   ")

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("title",))
        self.assertIn("Group title is required", errors[0]["msg"])


if __name__ == "__main__":
    unittest.main()
