"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This direct schema characterization does not update the executed-seed registry,
reduce canonical blockers, promote evidence, or complete the Stage.
"""

import unittest

from pydantic import ValidationError

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import ChannelMemberUpdateRequest


class TestChannelMemberUpdateUnsupportedRole(unittest.TestCase):
    """Characterize rejection of unsupported channel member role."""

    def test_unsupported_role_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            ChannelMemberUpdateRequest(role="owner")

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("role",))
        self.assertEqual(errors[0]["type"], "value_error")
        self.assertIn("Unsupported channel member role", errors[0]["msg"])


if __name__ == "__main__":
    unittest.main()
