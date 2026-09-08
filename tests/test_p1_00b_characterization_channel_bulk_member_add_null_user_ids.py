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

from api.routers.chat_schemas import ChannelBulkMemberAddRequest


class TestChannelBulkMemberAddNullUserIds(unittest.TestCase):
    """Characterize rejection of ChannelBulkMemberAddRequest with explicit null user_ids."""

    def test_null_user_ids_with_select_all_active_users_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            ChannelBulkMemberAddRequest(
                user_ids=None,
                select_all_active_users=True,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("user_ids",))
        self.assertEqual(errors[0]["type"], "list_type")
        self.assertIsNone(errors[0]["input"])


if __name__ == "__main__":
    unittest.main()
