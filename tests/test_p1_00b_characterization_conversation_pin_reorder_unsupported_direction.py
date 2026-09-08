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

from api.routers.chat_schemas import ConversationPinReorderUpdateRequest


class TestConversationPinReorderUnsupportedDirection(unittest.TestCase):
    """Characterize rejection of unsupported conversation pin reorder direction."""

    def test_unsupported_direction_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            ConversationPinReorderUpdateRequest(direction="side")

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("direction",))
        self.assertEqual(errors[0]["type"], "value_error")
        self.assertEqual(errors[0]["msg"], "Value error, Direction must be up or down")
        self.assertEqual(errors[0]["input"], "side")


if __name__ == "__main__":
    unittest.main()
