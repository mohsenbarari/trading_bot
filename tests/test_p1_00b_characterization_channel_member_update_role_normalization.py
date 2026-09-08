"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This direct schema characterization does not update the executed-seed registry,
reduce canonical blockers, promote evidence, or complete the Stage.
"""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import ChannelMemberUpdateRequest


class TestChannelMemberUpdateRoleNormalization(unittest.TestCase):
    """Characterize supported channel member role normalization."""

    def test_supported_role_normalization(self):
        cases = [
            ("  AdMiN  ", "admin"),
            ("  MeMbEr  ", "member"),
        ]
        for raw_role, expected_role in cases:
            with self.subTest(role=raw_role, expected=expected_role):
                request = ChannelMemberUpdateRequest(role=raw_role)
                self.assertEqual(request.role, expected_role)
                self.assertIs(request.remove_member, False)


if __name__ == "__main__":
    unittest.main()
