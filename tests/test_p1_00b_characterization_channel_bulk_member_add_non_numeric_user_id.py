"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This direct schema characterization does not update the executed-seed registry,
reduce canonical blockers, promote evidence, or complete the Stage.
"""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import ChannelBulkMemberAddRequest


class TestChannelBulkMemberAddNonNumericUserId(unittest.TestCase):
    """Characterize rejection of ChannelBulkMemberAddRequest with a non-numeric user_id."""

    def test_non_numeric_user_id_in_select_all_mode_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            ChannelBulkMemberAddRequest(
                user_ids=[9, "not-an-integer", 3],
                select_all_active_users=True,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("user_ids", 1))
        self.assertEqual(errors[0]["type"], "int_parsing")
        self.assertEqual(errors[0]["input"], "not-an-integer")


if __name__ == "__main__":
    unittest.main()
