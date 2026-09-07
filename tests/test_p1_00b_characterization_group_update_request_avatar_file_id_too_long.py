"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This module does not update the executed-seed registry, reduce a canonical
blocker, promote evidence, or mark P1-00b complete.
"""

import unittest

from pydantic import ValidationError

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import GroupUpdateRequest


class TestGroupUpdateRequestAvatarFileIdTooLong(unittest.TestCase):
    """Characterize rejection of avatar_file_id exceeding 36 characters for GroupUpdateRequest."""

    def test_group_update_request_avatar_file_id_too_long_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            GroupUpdateRequest(title="Valid Title", avatar_file_id="a" * 37)

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("avatar_file_id",))
        self.assertIn("36", errors[0]["msg"])


if __name__ == "__main__":
    unittest.main()
