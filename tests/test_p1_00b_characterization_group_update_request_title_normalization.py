"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This module does not update the executed-seed registry, reduce blockers,
promote evidence, change product behavior, or mark P1-00b complete.
"""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import GroupUpdateRequest


class TestGroupUpdateRequestTitleNormalization(unittest.TestCase):
    """Characterize title whitespace normalization for GroupUpdateRequest."""

    def test_group_update_request_title_normalization(self):
        request = GroupUpdateRequest(title="  Desk  ")

        self.assertEqual(request.title, "Desk")


if __name__ == "__main__":
    unittest.main()
